"""No external network, no MQTT connection, temporary PTT marker only."""
import json
import socket
import tempfile
import time
import unittest
import wave
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
import audio_library as lib
import audio_schedule as schedule
import mqtt_monitor as mm


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, value in [('ROOT', self.root/'library'), ('INBOX', self.root/'inbox')]:
            self.stack.enter_context(patch.object(lib, name, value))
        lib.initialize()
        self.stack.enter_context(patch.object(socket, 'create_connection', side_effect=AssertionError('network prohibited')))
        self.now = datetime(2026,11,13,10,0,tzinfo=schedule.TZ).timestamp()
        self.wav = lib.INBOX/'test.wav'
        with wave.open(str(self.wav),'wb') as w:
            w.setparams((1,2,16000,0,'NONE','not compressed')); w.writeframes(b'\x00\x10'*16000)

    def asset(self):
        return schedule.command({'action':'import','kind':'local','value':'test.wav'})['asset']['id']

    def enable(self):
        schedule.command({'action':'enable','enabled':True})

    def queue(self, asset=None):
        self.enable(); schedule.command({'action':'play','asset':asset or self.asset()})
        with schedule.database() as db:
            for row in db.execute('SELECT job,data FROM job_playlists').fetchall():
                data=json.loads(row['data']);data.pop('first_gap',None)
                db.execute('UPDATE job_playlists SET data=? WHERE job=?',(json.dumps(data),row['job']))

    def test_nas_worker_prepare_failure_has_no_transmission(self):
        self.queue();job=schedule.claim();job['gallery']={'directory':'a'*64,'count':1};m=self.monitor()
        with patch.object(mm.nas_gallery,'prepare',side_effect=mm.nas_gallery.GalleryError('图库图片数量不足')),patch.object(mm.cluster_runtime,'publish') as publish:
            m.process_audio_playback(job,m.activity_generation)
        publish.assert_not_called();self.assertFalse(m.audio_exclusive)
        self.assertEqual(schedule.snapshot()['jobs'][0]['result'],'图库图片数量不足')

    def test_nas_worker_prepared_paths_and_cleanup(self):
        self.queue();job=schedule.claim();job['gallery']={'directory':'a'*64,'count':1};m=self.monitor()
        temp=tempfile.TemporaryDirectory();target=Path(temp.name)/'image.wav';target.write_bytes(self.wav.read_bytes())
        with patch.object(mm.nas_gallery,'prepare',return_value=(temp,{'a'*64:target},[{'id':'a'*64,'name':'测试图片'}])),patch.object(mm.time,'sleep',side_effect=self.advance_clock),patch.object(mm.cluster_runtime,'publish',return_value=Mock(rc=0)) as publish:
            m.process_audio_playback(job,m.activity_generation)
        self.assertTrue(publish.called);self.assertFalse(target.exists())
        self.assertEqual(schedule.snapshot()['jobs'][0]['status'],'completed')
        self.assertEqual(schedule.snapshot()['jobs'][0]['gallery_selected'][0]['name'],'测试图片')

    def test_nas_plan_persists_and_selects_each_occurrence(self):
        gallery={'directory':'a'*64,'count':3,'recursive':True}
        with patch.object(schedule.nas_gallery,'directories',return_value={'directories':[{'id':'a'*64}]}):
            schedule.command({'action':'plan','gallery':gallery,'mode':'daily','time':'10:02'},now=self.now)
        self.enable();first=schedule.claim(now=self.now+120)
        self.assertEqual(first['gallery']['directory'],gallery['directory'])
        self.assertEqual(len(first['transition_texts']),2)
        schedule.finish(first['id'],'completed','test')
        second=schedule.claim(now=self.now+86400+120)
        self.assertEqual(second['gallery']['directory'],gallery['directory'])
        self.assertNotEqual(first['id'],second['id'])
        schedule.command({'action':'delete_plan','id':second['plan']})
        self.assertFalse(schedule.is_live(second['id']))

    def test_sstv_gain_defaults_setting_and_classification(self):
        a=self.asset();self.assertEqual(schedule.snapshot()['sstv_gain_db'],-24)
        self.assertNotIn(a,schedule.sstv_config()[1])
        schedule.command({'action':'set_sstv_type','asset':a,'is_sstv':True})
        self.assertIn(a,schedule.sstv_config()[1])
        schedule.command({'action':'set_sstv_gain','db':-12})
        self.assertEqual(schedule.snapshot()['sstv_gain_db'],-12)
        schedule.command({'action':'set_sstv_gain','db':0})
        self.assertEqual(schedule.sstv_config()[0],0)
        self.assertEqual(schedule.snapshot()['plans'],[])
        for bad in [True,-25,1,'-12']:
            with self.assertRaises(ValueError):schedule.command({'action':'set_sstv_gain','db':bad})

    def test_gain_applies_only_body_preserves_voice_cue(self):
        a=self.asset();self.queue(a);job=schedule.claim();m=self.monitor()
        job.update(opening_text='开场',closing_text='结束',chime_asset='default',first_gap=True,interval_seconds=60)
        schedule.command({'action':'set_sstv_type','asset':a,'is_sstv':True})
        original=mm.sstv_level.scale;seen=[]
        def scale(pcm,db):seen.append(pcm);return original(pcm,db)
        with patch.object(mm.sstv_level,'scale',side_effect=scale),patch.object(mm.speech,'synthesize_pcm_stream',side_effect=lambda text:iter([b'\x01\x00'*1600])),patch.object(mm.cluster_runtime,'publish',return_value=Mock(rc=0)),patch.object(mm.time,'sleep',side_effect=self.advance_clock):
            m.process_audio_playback(job,m.activity_generation)
        self.assertEqual(schedule.snapshot()['jobs'][0]['status'],'completed')
        self.assertEqual(sum(map(len,seen)),32000)
        self.assertTrue(all(p==b'\x00\x10'*(len(p)//2) for p in seen))

    def test_saved_speech_persists_without_activity(self):
        self.assertIsNone(schedule.snapshot()['saved_speech'])
        schedule.command({'action':'save_speech','opening_text':'准备接收','closing_text':'73'})
        self.assertEqual(schedule.snapshot()['saved_speech'],{'opening_text':'准备接收','closing_text':'73'})
        self.assertEqual(schedule.snapshot()['jobs'],[])
        self.assertEqual(schedule.snapshot()['plans'],[])
        self.assertFalse(schedule.snapshot()['enabled'])
        schedule.command({'action':'save_speech','opening_text':'','closing_text':''})
        self.assertEqual(schedule.snapshot()['saved_speech']['opening_text'],'')
        for value in [None,True,'字'*501]:
            with self.assertRaises(ValueError):schedule.command({'action':'save_speech','opening_text':value,'closing_text':'结束'})
        self.assertEqual(schedule.snapshot()['saved_speech']['closing_text'],'')

    def test_local_import_real_probe_and_conversion(self):
        a=self.asset(); data=schedule.snapshot()['assets'][0]
        self.assertEqual(data['duration'],1); self.assertTrue(lib.media_path(a).exists())
        with wave.open(str(lib.media_path(a))) as w:
            self.assertEqual((w.getnchannels(),w.getsampwidth(),w.getframerate()),(1,2,16000))

    def test_uploaded_audio_uses_conversion_without_transmission(self):
        a=schedule.command({'action':'import','kind':'upload','value':{'name':'本地声音.wav','data':self.wav.read_bytes()}})['asset']
        self.assertEqual(a['duration'],1)
        self.assertEqual(schedule.snapshot()['jobs'],[])
        with wave.open(str(lib.media_path(a['id']))) as w:self.assertEqual(w.getframerate(),16000)
        for value in [{'name':'../x.wav','data':b'x'},{'name':'x.wav','data':b''},{'name':'x.wav','data':b'invalid'}]:
            with self.assertRaises(ValueError):lib.import_media('upload',value)

    def test_path_symlink_hardlink_fifo_rejected(self):
        (lib.INBOX/'link').symlink_to(self.wav)
        import os
        os.link(self.wav,lib.INBOX/'hard');os.mkfifo(lib.INBOX/'fifo')
        for name in ['../test.wav',str(self.wav),'link','hard','fifo']:
            with self.subTest(name=name), self.assertRaises((ValueError,OSError)):
                lib.copy_local(name,self.root/'out')

    def test_invalid_format_no_artifact(self):
        (lib.INBOX/'bad.wav').write_text('not audio')
        with self.assertRaises(lib.MediaError):
            lib.import_media('local','bad.wav')
        self.assertEqual(list(lib.ROOT.glob('*.wav')),[])
        self.assertEqual(list(lib.ROOT.glob('.import-*')),[])

    def test_oversized_and_long_sources_rejected(self):
        with patch.object(lib,'MAX_BYTES',10), self.assertRaises(lib.MediaError):
            lib.import_media('local','test.wav')
        with patch.object(lib,'MAX_SECONDS',0.5), self.assertRaises(lib.MediaError):
            lib.import_media('local','test.wav')

    def test_cache_quota(self):
        with patch.object(lib,'MAX_CACHE',1), self.assertRaises(lib.MediaError):
            self.asset()

    def test_ipv6_tunnel_and_translation_are_not_public_targets(self):
        for address in ['::ffff:0:c612:4b','64:ff9b::7f00:1','2002:7f00:1::','::ffff:127.0.0.1']:
            self.assertFalse(lib.public_address(address))
        self.assertTrue(lib.public_address('2606:4700:4700::1111'))

    def test_url_protocol_and_private_addresses(self):
        for url in ['file:///etc/passwd','http://u:p@example.com/a','http://example.com:8080/a','https://example.com/a\nheader', 'http://127.0.0.1/a','http://169.254.169.254/a','http://[::1]/a','http://10.1.1.1/a']:
            with self.subTest(url=url),self.assertRaises(lib.MediaError):
                lib.public_target(url)
        with patch.object(lib.subprocess,'run',return_value=Mock(stdout=b'["1.1.1.1","10.0.0.1"]')),self.assertRaises(lib.MediaError):
            lib.public_target('https://example.com/a')

    def test_url_mock_download_actual_decode_redacts_secret(self):
        def download(command, config, target, **kwargs):
            self.assertIn('--resolve',command);self.assertIn('--noproxy',command)
            self.assertNotIn('https://example.com/a?token=SECRET',command)
            target.write_bytes(self.wav.read_bytes())
        with patch.object(lib,'public_target',return_value=('example.com',443,'1.1.1.1')),patch.object(lib,'bounded_download',side_effect=download):
            schedule.command({'action':'import','kind':'url','value':'https://example.com/a?token=SECRET'})
        self.assertNotIn('SECRET',json.dumps(schedule.snapshot()))
        self.assertEqual(schedule.snapshot()['assets'][0]['source'],'URL: example.com')

    def test_redirect_timeout_and_stream_size_cap(self):
        import sys
        for code in ["import sys;sys.stdout.buffer.write(b'bad302')", "import sys;sys.stdout.buffer.write(b'x'*100+b'200')"]:
            with patch.object(lib,'MAX_BYTES',30),self.assertRaises(lib.MediaError):
                lib.bounded_download([sys.executable,'-c',code],b'',self.root/'out')
        with patch.object(lib,'public_target',return_value=('example.com',443,'1.1.1.1')),patch.object(lib,'bounded_download',side_effect=TimeoutError()),self.assertRaises(lib.MediaError):
            lib.download('https://example.com/a',self.root/'out')

    def test_next_occurrences_and_two_minute_rule(self):
        for plan,day in [({'mode':'once','time':'10:02','date':'2026-11-13'},13),({'mode':'daily','time':'09:00'},14),({'mode':'weekly','time':'10:02','weekday':4},13)]:
            stamp=schedule.next_time(plan,self.now,True)
            self.assertEqual(datetime.fromtimestamp(stamp,schedule.TZ).day,day)
        for mode in ['once','daily','weekly']:
            with self.assertRaises(ValueError):schedule.next_time({'mode':mode,'time':'10:01','date':'2026-11-13','weekday':4},self.now,True)

    def test_plan_at_most_once_and_no_late_catchup(self):
        a=self.asset();self.enable()
        schedule.command({'action':'plan','asset':a,'mode':'daily','time':'10:02'},now=self.now)
        job=schedule.claim(now=self.now+120);self.assertIsNotNone(job)
        self.assertIsNone(schedule.claim(now=self.now+120));self.assertIsNone(schedule.claim(now=self.now+86400+130))
        self.assertEqual(len(schedule.snapshot()['jobs']),1)

    def test_restart_never_resumes_jobs_or_past_schedule(self):
        a=self.asset();self.queue(a)
        schedule.command({'action':'plan','asset':a,'mode':'once','time':'10:02','date':'2026-11-13'},now=self.now)
        with patch.object(schedule.time,'time',return_value=self.now+180):schedule.recover()
        state=schedule.snapshot();self.assertEqual(state['jobs'][0]['status'],'interrupted')
        self.assertFalse(state['plans'][0]['enabled']);self.assertIsNone(schedule.claim(now=self.now+180))

    def test_disable_and_emergency_cancel(self):
        self.queue();job=schedule.claim();self.assertTrue(schedule.is_live(job['id']))
        schedule.command({'action':'emergency_stop'})
        self.assertFalse(schedule.is_live(job['id']));self.assertFalse(schedule.snapshot()['enabled'])

    def test_busy_and_expired_skip(self):
        self.queue();self.assertIsNone(schedule.claim('频道忙'))
        self.assertEqual(schedule.snapshot()['jobs'][0]['status'],'skipped')
        self.queue();self.assertIsNone(schedule.claim(now=time.time()+10))

    def test_scheduled_takeover_busy_but_hard_blockers_preserved(self):
        a=self.asset();self.enable()
        schedule.command({'action':'plan','asset':a,'mode':'daily','time':'10:02'},now=self.now)
        job=schedule.claim(now=self.now+120,busy='频道忙')
        self.assertIsNotNone(job);self.assertTrue(job['plan'])
        self.assertIsNone(schedule.claim(now=self.now+120,busy='频道忙'))
        schedule.finish(job['id'],'completed','test')
        self.queue(a)
        self.assertIsNone(schedule.claim(busy='频道忙'))
        self.assertIsNone(schedule.claim('安全门关闭',now=self.now+86400+120,busy='频道忙'))

    def test_scheduled_monitor_reserves_and_cancels_old_worker(self):
        a=self.asset();self.enable()
        schedule.command({'action':'plan','asset':a,'mode':'daily','time':'10:02'},now=self.now)
        m=self.monitor();m.active_frames=2;m.channel_last_activity=self.now+120
        worker=Mock();m.voice_worker=worker;generation=m.activity_generation
        with patch.object(schedule.time,'time',return_value=self.now+120),patch.object(mm.threading,'Thread') as thread:
            self.assertTrue(m.check_audio_playback())
            job=thread.call_args.kwargs['args'][0]
        self.assertIs(job['_previous_worker'],worker)
        self.assertEqual(m.activity_generation,generation+1)
        self.assertEqual(m.active_frames,0);self.assertTrue(m.audio_exclusive)

    def test_takeover_waits_for_previous_worker_before_transmitting(self):
        self.queue();job=schedule.claim();m=self.monitor()
        previous=Mock();previous.is_alive.side_effect=[True,True,False]
        job['_previous_worker']=previous
        def publish(*args):
            self.assertEqual(previous.is_alive.call_count,3)
            return Mock(rc=0)
        with patch.object(mm.time,'sleep',side_effect=self.advance_clock),patch.object(mm.cluster_runtime,'publish',side_effect=publish):
            m.process_audio_playback(job,m.activity_generation)
        self.assertEqual(schedule.snapshot()['jobs'][0]['status'],'completed')

    def test_overlapping_plans_one_claim(self):
        a=self.asset();self.enable()
        for _ in range(2):schedule.command({'action':'plan','asset':a,'mode':'once','time':'10:02','date':'2026-11-13'},now=self.now)
        self.assertIsNotNone(schedule.claim(now=self.now+120))
        self.assertEqual(sorted(j['status'] for j in schedule.snapshot()['jobs']),['playing','skipped'])

    def advance_clock(self, seconds):
        self.virtual_clock += seconds

    def monitor(self):
        self.stack.enter_context(patch.object(mm.audio_channel_guard, "Lease"))
        self.virtual_clock = 100.0
        self.stack.enter_context(patch.object(mm.time,'monotonic',side_effect=lambda:self.virtual_clock))
        self.stack.enter_context(patch.object(mm.callsign_policy,'load_stats',return_value={}))
        self.stack.enter_context(patch.object(mm,'HOURLY_STATE_PATH',self.root/'hourly'))
        self.stack.enter_context(patch.object(mm,'PTT_MARKER',self.root/'ptt'))
        self.stack.enter_context(patch.object(mm,'AUDIO_PLAYBACK_ENABLED',True))
        self.stack.enter_context(patch.object(mm,'AUTO_ENABLED',True))
        self.stack.enter_context(patch.object(mm,'NET_CHECKIN_RUNTIME_ENABLED',False))
        m=mm.Monitor();m.connected=True;m.client=Mock();m.write_state=Mock()
        m.processing=True;return m

    def test_mock_mqtt_e2e_and_radpcm(self):
        self.queue();job=schedule.claim();m=self.monitor();packets=[]
        def publish(client,topic,payload,kind):
            self.assertTrue(mm.PTT_MARKER.exists());self.assertTrue(m.processing)
            self.assertEqual(kind,'local_only');packets.append(payload);return Mock(rc=0)
        with patch.object(mm.cluster_runtime,'publish',side_effect=publish),patch.object(mm.time,'sleep',side_effect=self.advance_clock) as sleep:
            m.process_audio_playback(job,0)
            frames,_=mm.fmo_codec.voice_frames(packets,65535,'AI-FMO')
        self.assertTrue(frames);self.assertEqual({f[0] for f in frames},{2})
        self.assertAlmostEqual(sum(c.args[0] for c in sleep.call_args_list),mm.fmo_codec.media_duration_ms(frames)/1000)
        self.assertEqual(schedule.snapshot()['jobs'][0]['status'],'completed')
        self.assertFalse(m.processing);self.assertFalse(mm.PTT_MARKER.exists())

    def test_activity_ignores_inbound_until_complete(self):
        self.queue();job=schedule.claim();m=self.monitor();before=m.activity_generation
        payloads,_=mm.fmo_codec.build_radpcm_payloads(b'\0'*2560,base=10,uid=123,callsign='BG1ABC')
        def publish(*args):
            self.assertTrue(m.audio_exclusive);m.receive(payloads[0])
            self.assertEqual(m.activity_generation,before);self.assertEqual(m.active_frames,0)
            self.assertEqual(len(mm.fmo_codec.parse_packet(args[2])[3]),1)
            return Mock(rc=0)
        with patch.object(mm.cluster_runtime,'publish',side_effect=publish),patch.object(mm.time,'sleep',side_effect=self.advance_clock):m.process_audio_playback(job,0)
        self.assertFalse(m.audio_exclusive);self.assertFalse(m.processing)
        self.assertEqual(schedule.snapshot()['jobs'][0]['status'],'completed')

    def test_guard_failure_never_transmits(self):
        self.queue();job=schedule.claim();m=self.monitor()
        mm.audio_channel_guard.Lease.return_value.refresh.side_effect=RuntimeError('活动独占保护不可用，已停止播放')
        with patch.object(mm.cluster_runtime,'publish') as publish:m.process_audio_playback(job,0)
        publish.assert_not_called();self.assertFalse(m.audio_exclusive)
        mm.audio_channel_guard.Lease.return_value.close.assert_called_once()

    def test_packet_clock_compensates_per_packet_processing_delay(self):
        with wave.open(str(self.wav),'wb') as wav:
            wav.setparams((1,2,16000,0,'NONE','none'));wav.writeframes(b'\0'*(37*32000))
        self.queue();job=schedule.claim();m=self.monitor();sent=[]
        def publish(client,topic,payload,kind):
            frames=int.from_bytes(payload[mm.HEADER.size+12:mm.HEADER.size+14],'little')
            sent.append((self.virtual_clock,frames*.08))
            self.advance_clock(.0085) # measured production overhead, approximately 8.55 ms
            return Mock(rc=0)
        with patch.object(mm.cluster_runtime,'publish',side_effect=publish),patch.object(mm.time,'sleep',side_effect=self.advance_clock):
            m.process_audio_playback(job,0)
        for previous,current in zip(sent,sent[1:]):
            self.assertAlmostEqual(current[0]-previous[0],previous[1],places=6)
        self.assertAlmostEqual(self.virtual_clock-100,sum(duration for _,duration in sent),places=6)
        self.assertEqual(schedule.snapshot()['jobs'][0]['status'],'completed')

    def test_full_audio_over_sixty_seconds_is_not_truncated(self):
        with wave.open(str(self.wav),'wb') as wav:
            wav.setparams((1,2,16000,0,'NONE','none'));wav.writeframes(b'\0'*(65*32000))
        self.queue();job=schedule.claim();m=self.monitor()
        with patch.object(mm.cluster_runtime,'publish',return_value=Mock(rc=0)),patch.object(mm.time,'sleep',side_effect=self.advance_clock) as sleep:
            m.process_audio_playback(job,0)
        self.assertGreater(sum(c.args[0] for c in sleep.call_args_list),65)
        self.assertEqual(schedule.snapshot()['jobs'][0]['status'],'completed')

    def test_hourly_night_and_voice_share_processing_guard(self):
        m=self.monitor()
        with patch.object(mm,'HOURLY_ENABLED',True),patch.object(mm.control_state,'enabled',return_value=True),patch.object(mm.threading,'Thread') as thread:
            self.assertFalse(m.check_hourly_announcement(datetime(2026,11,13,12,0,tzinfo=schedule.TZ)))
            self.assertFalse(m.check_night_notice(datetime(2026,11,13,23,50,tzinfo=schedule.TZ)))
            thread.assert_not_called()

    def test_preemption_stop_mqtt_failure_disconnect_release(self):
        a=self.asset()
        for cause in ['checkin','stop','human','disconnect','mqtt']:
            with self.subTest(cause=cause):
                self.queue(a);job=schedule.claim();m=self.monitor()
                def publish(*args):
                    if cause=='checkin':m.audio_checkin_blocked=lambda:True
                    if cause=='stop':schedule.command({'action':'emergency_stop'})
                    if cause=='human':m.activity_generation+=1
                    if cause=='disconnect':m.connected=False
                    return Mock(rc=1 if cause=='mqtt' else 0)
                with patch.object(mm.cluster_runtime,'publish',side_effect=publish) as send,patch.object(mm.time,'sleep',side_effect=self.advance_clock):
                    m.process_audio_playback(job,0)
                self.assertEqual(send.call_count,1);self.assertFalse(m.processing);self.assertFalse(mm.PTT_MARKER.exists())
                self.assertIn(schedule.snapshot()['jobs'][0]['status'],['failed','cancelled'])

    def program_job(self, **extra):
        ids=[self.asset(),self.asset()];self.enable()
        schedule.command({'action':'play','assets':ids,'opening_text':'开始接收 SSTV','interval_seconds':60,'chime_asset':'off',**extra})
        job = schedule.claim()
        job.pop('first_gap', None)  # Explicit legacy fixture.
        job.pop('transition_texts', None)  # Exercise compatibility with existing saved playlists.
        return job

    def test_playlist_plan_preserves_order_and_blocks_deleting_later_asset(self):
        ids=[self.asset(),self.asset()];self.enable()
        schedule.command({'action':'plan','assets':ids[::-1],'opening_text':'开场','interval_seconds':60,
                          'mode':'once','date':'2026-11-13','time':'10:02'},now=self.now)
        job=schedule.claim(now=self.now+120)
        self.assertEqual(job['assets'],ids[::-1]);self.assertEqual(job['opening_text'],'开场')
        self.assertEqual(job['interval_seconds'],60)
        with self.assertRaises(ValueError):schedule.command({'action':'delete_asset','asset':ids[0]})

    def test_playlist_validation(self):
        a=self.asset()
        for extra in [{'assets':[]},{'assets':[a,a]},{'assets':[a,'missing']},
                      {'interval_seconds':30},{'interval_seconds':-1},{'interval_seconds':601},{'interval_seconds':True},
                      {'opening_text':'字'*501},{'closing_text':'字'*501},{'closing_text':False},{'interval_seconds':10},{'interval_seconds':181}]:
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                schedule.command({'action':'plan','assets':[a],'mode':'daily','time':'10:02',**extra},now=self.now)

    def test_opening_then_playlist_gap_has_no_ptt(self):
        job=self.program_job();m=self.monitor();sent=[];gaps=[]
        def sleep(seconds):
            self.advance_clock(seconds)
            if seconds==.1:
                self.assertFalse(mm.PTT_MARKER.exists());self.assertTrue(m.processing);gaps.append(len(sent))
        with patch.object(mm.speech,'synthesize_pcm_stream',return_value=iter([b'\0'*3200])) as tts,patch.object(mm.cluster_runtime,'publish',side_effect=lambda *args:(sent.append(args[2]) or Mock(rc=0))),patch.object(mm.time,'sleep',side_effect=sleep):
            m.process_audio_playback(job,0)
        tts.assert_called_once_with('开始接收 SSTV');self.assertEqual(len(gaps),610)
        self.assertEqual(len(set(gaps)),2) # opening gap and media gap, no packets during either
        self.assertEqual(schedule.snapshot()['jobs'][0]['status'],'completed')

    def test_automatic_transitions_and_closing_order(self):
        ids=[self.asset(),self.asset(),self.asset()];self.enable()
        schedule.command({'action':'play','assets':ids,'opening_text':'开场','closing_text':'结束','interval_seconds':60})
        job=schedule.claim();m=self.monitor();events=[]
        self.assertEqual(len(job['transition_texts']),2)
        with patch.object(mm.speech,'synthesize_pcm_stream',side_effect=lambda text:iter([b'\0'*3200])) as tts,patch.object(mm.cluster_runtime,'publish',return_value=Mock(rc=0)),patch.object(mm.time,'sleep',side_effect=self.advance_clock),patch.object(schedule,'progress',side_effect=lambda ident,text:events.append(text)):
            m.process_audio_playback(job,0)
        self.assertEqual([c.args[0] for c in tts.call_args_list],['开场',*job['transition_texts'],'结束'])
        self.assertLess(events.index('正在播放第 2 段提示语'), events.index('段间等待 60 秒（下一段 2/3）'))
        self.assertLess(events.index('段间等待 60 秒（下一段 2/3）'), events.index('正在播放第 2/3 段音频'))
        played=[e for e in events if e.startswith('正在播放')]
        self.assertEqual(played,['正在播放 AI 开场提示词','正在播放提示音（距下一段 3 秒）','正在播放第 1/3 段音频','正在播放第 2 段提示语','正在播放提示音（距下一段 3 秒）','正在播放第 2/3 段音频','正在播放第 3 段提示语','正在播放提示音（距下一段 3 秒）','正在播放第 3/3 段音频','正在播放 AI 结束话术'])
        self.assertEqual(schedule.snapshot()['jobs'][0]['status'],'completed')
        self.assertFalse(mm.PTT_MARKER.exists())

    def test_chime_deadline_and_custom_asset_protection(self):
        for choice in ('default','custom'):
            with self.subTest(choice=choice):
                cue=self.asset() if choice=='custom' else 'default'
                job=self.program_job(chime_asset=cue);m=self.monitor();events=[]
                if choice=='custom':
                    with self.assertRaises(ValueError):schedule.command({'action':'delete_asset','asset':cue})
                with patch.object(mm.speech,'synthesize_pcm_stream',return_value=iter([b'\0'*3200])),patch.object(mm.cluster_runtime,'publish',return_value=Mock(rc=0)),patch.object(mm.time,'sleep',side_effect=self.advance_clock),patch.object(schedule,'progress',side_effect=lambda ident,text:events.append((text,self.virtual_clock))):
                    m.process_audio_playback(job,0)
                gap=next(t for text,t in events if text.startswith('段间等待'))
                cue_at=next(t for text,t in events if '距下一段' in text)
                next_at=next(t for text,t in events if text=='正在播放第 2/2 段音频')
                self.assertAlmostEqual(cue_at-gap,57,places=5)
                self.assertAlmostEqual(next_at-cue_at,3,places=5)
                self.assertEqual(schedule.snapshot()['jobs'][0]['status'],'completed')

    def test_real_progress_timeline_records_phases(self):
        job=self.program_job(chime_asset='default');m=self.monitor()
        with patch.object(mm.speech,'synthesize_pcm_stream',return_value=iter([b'\0'*3200])),patch.object(mm.cluster_runtime,'publish',return_value=Mock(rc=0)),patch.object(mm.time,'sleep',side_effect=self.advance_clock):
            m.process_audio_playback(job,0)
        result=schedule.snapshot()['jobs'][0];progress=result['_progress']
        self.assertEqual(result['status'],'completed')
        self.assertEqual(progress['index'],len(progress['phases'])-1)
        self.assertEqual([p['kind'] for p in progress['phases']],['voice','gap','sstv','gap','cue','gap','sstv'])
        self.assertAlmostEqual(sum(p['duration'] for p in progress['phases'][3:6]),60)

    def test_closing_preparation_failure_never_transmits(self):
        job=self.program_job(closing_text='结束');m=self.monitor()
        with patch.object(mm.speech,'synthesize_pcm_stream',side_effect=[iter([b'\0'*3200]),RuntimeError('secret')]),patch.object(mm.cluster_runtime,'publish') as publish:
            m.process_audio_playback(job,0)
        publish.assert_not_called()
        self.assertEqual(schedule.snapshot()['jobs'][0]['status'],'failed')

    def test_stop_during_blocked_tts_releases_playback_reservation(self):
        import threading
        job=self.program_job();m=self.monitor();release=threading.Event()
        def stream(text):
            schedule.command({'action':'stop'})
            release.wait(3)
            yield b'\0'*3200
        try:
            with patch.object(mm.speech,'synthesize_pcm_stream',side_effect=stream),patch.object(mm.cluster_runtime,'publish') as publish:
                started=time.perf_counter();m.process_audio_playback(job,0)
                self.assertLess(time.perf_counter()-started,1)
                self.assertFalse(m.processing);publish.assert_not_called()
        finally:
            release.set();m.audio_intro_worker.join(3)

    def test_tts_failure_and_overlong_opening_never_transmit(self):
        for failure in ['network','long']:
            job=self.program_job();m=self.monitor()
            with patch.object(mm.speech,'synthesize_pcm_stream',side_effect=RuntimeError('provider secret') if failure=='network' else None,return_value=iter([b'\0'*(46*32000)])),patch.object(mm.cluster_runtime,'publish') as publish:
                m.process_audio_playback(job,0)
            publish.assert_not_called();self.assertFalse(m.processing)
            self.assertNotIn('provider secret',json.dumps(schedule.snapshot()))
            self.assertEqual(schedule.snapshot()['jobs'][0]['status'],'failed')

    def test_stop_or_checkin_during_gap_aborts_rest_of_playlist(self):
        for reason in ['stop','checkin','human']:
            job=self.program_job(opening_text='');m=self.monitor();sent=[];at_gap=[]
            def sleep(seconds):
                self.advance_clock(seconds)
                if seconds==.1:
                    self.assertFalse(mm.PTT_MARKER.exists());at_gap.append(len(sent))
                    if reason=='stop':schedule.command({'action':'stop'})
                    elif reason=='checkin':m.audio_checkin_blocked=lambda:True
                    else:m.activity_generation+=1
            with patch.object(mm.cluster_runtime,'publish',side_effect=lambda *args:(sent.append(args[2]) or Mock(rc=0))),patch.object(mm.time,'sleep',side_effect=sleep):
                m.process_audio_playback(job,0)
            self.assertEqual(len(sent),at_gap[0]);self.assertFalse(m.processing)
            self.assertIn(schedule.snapshot()['jobs'][0]['status'],['failed','cancelled'])

    def test_first_preparation_matrix(self):
        ids=[self.asset(),self.asset()];custom=self.asset();self.enable()
        for count in (1,2):
            for opening in ('','开场'):
                for interval in (60,90,120,180):
                    for choice in ('default',custom,'off'):
                        with self.subTest(count=count,opening=opening,interval=interval,choice=choice):
                            schedule.command({'action':'play','assets':ids[:count],'opening_text':opening,'closing_text':'结束','interval_seconds':interval,'chime_asset':choice})
                            job=schedule.claim();self.assertTrue(job['first_gap']);m=self.monitor();events=[]
                            original=schedule.progress
                            def progress(ident,text):
                                events.append((text,self.virtual_clock));return original(ident,text)
                            def sleep(seconds):
                                if events and ('准备' in events[-1][0] or '等待' in events[-1][0]):
                                    self.assertFalse(mm.PTT_MARKER.exists())
                                    self.assertTrue(m.audio_exclusive)
                                self.advance_clock(seconds)
                            started=self.virtual_clock
                            with patch.object(m,'prepare_audio_opening',return_value=b'\0'*3200),patch.object(mm.cluster_runtime,'publish',return_value=Mock(rc=0)),patch.object(mm.time,'sleep',side_effect=sleep),patch.object(schedule,'progress',side_effect=progress):
                                m.process_audio_playback(job,0)
                            result=next(j for j in schedule.snapshot()['jobs'] if j['id']==job['id'])
                            self.assertEqual(result['status'],'completed')
                            prep=next(t for label,t in events if label.startswith('首段准备'))
                            first=next(t for label,t in events if label==f'正在播放第 1/{count} 段音频')
                            self.assertAlmostEqual(first-prep,interval,places=5)
                            cues=[t for label,t in events if '距下一段' in label]
                            self.assertEqual(len(cues),count if choice!='off' else 0)
                            if cues:self.assertAlmostEqual(first-cues[0],3,places=5)
                            phases=result['_progress']['phases']
                            self.assertAlmostEqual(sum(p['duration'] for p in phases),self.virtual_clock-started,places=4)
                            self.assertEqual(result['_progress']['index'],len(phases)-1)
                            self.assertFalse(m.audio_exclusive)

    def test_stop_and_checkin_interrupt_first_preparation(self):
        for reason in ('stop','checkin'):
            job=self.program_job(opening_text='');job['first_gap']=True;m=self.monitor()
            def sleep(seconds):
                self.assertFalse(mm.PTT_MARKER.exists());self.assertTrue(m.audio_exclusive)
                self.advance_clock(seconds)
                if reason=='stop':schedule.command({'action':'stop'})
                else:m.audio_checkin_blocked=lambda:True
            with patch.object(mm.cluster_runtime,'publish') as publish,patch.object(mm.time,'sleep',side_effect=sleep):
                m.process_audio_playback(job,0)
            publish.assert_not_called();self.assertFalse(m.audio_exclusive)
            self.assertFalse(mm.PTT_MARKER.exists())

    def test_first_gap_counts_toward_duration_limit(self):
        ident=self.asset()
        with schedule.database() as db:
            row=json.loads(db.execute('SELECT data FROM assets WHERE id=?',(ident,)).fetchone()[0])
            row['duration']=7150
            db.execute('UPDATE assets SET data=? WHERE id=?',(json.dumps(row),ident))
            with self.assertRaises(ValueError):schedule.playlist({'assets':[ident]},db)

    def test_storage_failure_disables_only_audio_scheduler(self):
        import sqlite3
        m=self.monitor();m.processing=False
        with patch.object(schedule,'claim',side_effect=sqlite3.DatabaseError('corrupt')):
            self.assertFalse(m.check_audio_playback())
        self.assertFalse(m.audio_runtime_ready);self.assertFalse(m.processing)
        self.assertTrue(m.connected)

    def test_no_start_when_existing_work_checkin_offline_gate_closed(self):
        a=self.asset()
        for cause in ['processing','active_frames','idle','checkin','offline','gate']:
            self.queue(a);m=self.monitor();m.processing=False
            if cause=='processing':m.processing=True
            if cause=='active_frames':m.active_frames=1
            if cause=='idle':m.channel_last_activity=time.time()
            if cause=='checkin':m.audio_checkin_blocked=lambda:True
            if cause=='offline':m.connected=False
            with patch.object(mm,'AUDIO_PLAYBACK_ENABLED',cause!='gate'),patch.object(mm.threading,'Thread') as thread:
                self.assertFalse(m.check_audio_playback());thread.assert_not_called()

if __name__=='__main__':unittest.main()
