"""Shared SQLite queue: transactional at-most-once scheduling, no MQTT here."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3
import time
import uuid
import audio_library as media
import sstv_catalog
import nas_gallery
import sstv_level

TZ = timezone(timedelta(hours=8))


@contextmanager
def database():
    media.initialize()
    connection = sqlite3.connect(media.ROOT / 'schedule.sqlite3', timeout=3)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript('''
        CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, data TEXT NOT NULL, next REAL NOT NULL, enabled INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, asset TEXT NOT NULL, plan TEXT, created REAL NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS job_playlists(job TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT OR IGNORE INTO settings VALUES('enabled','false');
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at REAL NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL);
        ''')
        connection.execute('BEGIN IMMEDIATE')
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def audit(db, event, detail=''):
    db.execute('INSERT INTO audit(at,event,detail) VALUES(?,?,?)', (time.time(), event, detail))
    db.execute('DELETE FROM audit WHERE id NOT IN (SELECT id FROM audit ORDER BY id DESC LIMIT 1000)')


def enabled(db):
    return db.execute("SELECT value FROM settings WHERE key='enabled'").fetchone()[0] == 'true'


def next_time(plan, now, validate=False):
    try:
        hour, minute = map(int, plan['time'].split(':'))
        day = datetime.fromtimestamp(now, TZ).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if plan['mode'] == 'once':
            day = datetime.combine(datetime.strptime(plan['date'], '%Y-%m-%d').date(), day.timetz())
        elif plan['mode'] == 'daily':
            if day.timestamp() <= now:
                day += timedelta(days=1)
        elif plan['mode'] == 'weekly':
            weekday = int(plan['weekday'])
            if not 0 <= weekday <= 6:
                raise ValueError()
            day += timedelta(days=(weekday - day.weekday()) % 7)
            if day.timestamp() <= now:
                day += timedelta(days=7)
        else:
            raise ValueError()
        stamp = day.timestamp()
        if validate and stamp < now + 120:
            raise ValueError('计划开始时间必须至少晚于当前时间 2 分钟（北京时间）')
        return stamp
    except (KeyError, TypeError, ValueError) as exc:
        if str(exc).startswith('计划'):
            raise
        raise ValueError('请填写有效的单次/每天/每周计划及北京时间') from None


def playlist(body, db):
    gallery = {**nas_gallery.validate(body['gallery']),'name':body['gallery'].get('name','图库')} if body.get('gallery') is not None else None
    assets = ['nas-gallery-'+str(i) for i in range(gallery['count'])] if gallery else body.get('assets', [body.get('asset', '')])
    if (not isinstance(assets, list) or not 1 <= len(assets) <= 50 or
            any(not isinstance(a, str) for a in assets) or len(set(assets)) != len(assets)):
        raise ValueError('请选择 1–50 段音频，同一计划不重复选择同一音频')
    opening = body.get('opening_text', '')
    interval = body.get('interval_seconds', 60)
    closing = body.get('closing_text', '')
    if not isinstance(opening, str) or len(opening.strip()) > 500:
        raise ValueError('开场提示词最多 500 字')
    if not isinstance(closing, str) or len(closing.strip()) > 500:
        raise ValueError('结束话术最多 500 字')
    if type(interval) is not int or interval not in (60, 90, 120, 180):
        raise ValueError('音频间隔请选择 60、90、120 或 180 秒')
    transitions = [f'本段图片发送完毕，间隔 {interval} 秒后继续发送，请各位友台保持接收。' for _ in assets[1:]]
    chime = body.get('chime_asset', 'default')
    if chime not in ('default', 'off'):
        if not isinstance(chime, str):raise ValueError('提示音选择无效')
        row = db.execute('SELECT data FROM assets WHERE id=?', (chime,)).fetchone()
        if not row or not 0 < json.loads(row[0])['duration'] <= 2:
            raise ValueError('自定义提示音须先上传，且时长不超过 2 秒')
    duration = 0
    for asset in ([] if gallery else assets):
        row = db.execute('SELECT data FROM assets WHERE id=?', (asset,)).fetchone()
        if not row:
            raise ValueError('请先选择已导入的音频')
        duration += json.loads(row[0])['duration']
    if gallery:duration = 37 * len(assets)
    # First preparation is a full interval; cue duration is contained in each gap.
    # Round up media/TTS packet padding conservatively for the two-hour guard.
    if duration + interval * len(assets) + 47 * (bool(opening.strip()) + bool(closing.strip()) + len(transitions)) + 2 * len(assets) + bool(closing.strip()) > 7200:
        raise ValueError('一组播放含间隔和所有话术最多 2 小时，请拆分计划')
    return {**({'gallery':gallery} if gallery else {}), 'asset': assets[0], 'assets': assets, 'opening_text': opening.strip(), 'interval_seconds': interval, 'transition_texts': transitions, 'closing_text': closing.strip(), 'chime_asset': chime, 'first_gap': True}


def job_program(db, job):
    row = db.execute('SELECT data FROM job_playlists WHERE job=?', (job['id'],)).fetchone()
    return json.loads(row[0]) if row else {'assets': [job['asset']], 'opening_text': '', 'interval_seconds': 0}


def sstv_config():
    with database() as db:
        row=db.execute("SELECT value FROM settings WHERE key='sstv_gain_db'").fetchone()
        gain=sstv_level.validate(json.loads(row[0])) if row else -24
        ids={r['id'] for r in db.execute('SELECT id,data FROM assets') if sstv_level.is_sstv(json.loads(r['data']))}
        return gain,ids


def snapshot():
    with database() as db:
        return {'enabled': enabled(db), 'timezone': 'Asia/Shanghai',
                'sstv_gain_db': (json.loads(row['value']) if (row := db.execute("SELECT value FROM settings WHERE key='sstv_gain_db'").fetchone()) else -24),
                'saved_speech': (json.loads(row['value']) if (row := db.execute("SELECT value FROM settings WHERE key='saved_speech'").fetchone()) else None),
                'assets': [json.loads(r['data']) for r in db.execute('SELECT data FROM assets')],
                'plans': [{**json.loads(r['data']), 'id': r['id'], 'next': r['next'], 'enabled': bool(r['enabled'])}
                          for r in db.execute('SELECT * FROM plans ORDER BY next')],
                'jobs': [{**dict(r), **job_program(db, r)} for r in db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 20')],
                'audit': [dict(r) for r in db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 30')],
                'local_files': media.local_names()}


def command(body, now=None):
    now = time.time() if now is None else now
    if not isinstance(body, dict):
        raise ValueError('请求必须是 JSON 对象')
    action = body.get('action')
    if action in {'play','plan'} and body.get('gallery') is not None:
        gallery=nas_gallery.validate(body['gallery'])
        directory=next((d for d in nas_gallery.directories()['directories'] if d['id']==gallery['directory']),None)
        if directory is None:raise ValueError('NAS 图库目录不存在，请重新读取目录')
        body={**body,'gallery':{**gallery,'name':str(directory.get('path') or directory.get('name') or '全部图库')[:500]}}
    if 'is_sstv' in body and type(body['is_sstv']) is not bool:raise ValueError('SSTV 类型标记无效')
    if action in {'import', 'import_sstv'}:
        asset = (sstv_catalog.import_item(body.get('id')) if action == 'import_sstv'
                 else media.import_media(body.get('kind'), body.get('value')))
        if action=='import_sstv':asset['is_sstv']=True
        elif 'is_sstv' in body:asset['is_sstv']=body['is_sstv']
        try:
            with database() as db:
                db.execute('INSERT INTO assets VALUES(?,?)', (asset['id'], json.dumps(asset, ensure_ascii=False)))
                audit(db, 'import', asset['id'])
        except BaseException:
            media.media_path(asset['id']).unlink(missing_ok=True)
            raise
        return {'asset': asset}
    with database() as db:
        if action == 'set_sstv_gain':
            value=sstv_level.validate(body.get('db'))
            db.execute("INSERT OR REPLACE INTO settings VALUES('sstv_gain_db',?)",(json.dumps(value),))
            audit(db,'sstv_gain',str(value)+' dB')
        elif action == 'set_sstv_type':
            row=db.execute('SELECT data FROM assets WHERE id=?',(body.get('asset'),)).fetchone()
            if not row or type(body.get('is_sstv')) is not bool:raise ValueError('音频或SSTV类型标记无效')
            asset=json.loads(row[0]);asset['is_sstv']=body['is_sstv']
            db.execute('UPDATE assets SET data=? WHERE id=?',(json.dumps(asset),body['asset']))
            audit(db,'sstv_type',body['asset']+': '+str(body['is_sstv']))
        elif action == 'save_speech':
            values = {}
            for key in ('opening_text', 'closing_text'):
                value = body.get(key)
                if not isinstance(value, str) or len(value.strip()) > 500:
                    raise ValueError('开场和结束话术须为文本，每条最多 500 字')
                values[key] = value.strip()
            db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('saved_speech',?)", (json.dumps(values, ensure_ascii=False),))
            audit(db, 'save_speech', '开场和结束话术已保存')
        elif action in {'stop', 'emergency_stop'}:
            db.execute("UPDATE jobs SET status='cancelled', result='管理员停止' WHERE status IN ('queued','playing')")
            if action == 'emergency_stop':
                db.execute("UPDATE settings SET value='false' WHERE key='enabled'")
            audit(db, action)
        elif action == 'enable':
            if type(body.get('enabled')) is not bool:
                raise ValueError('开关必须为布尔值')
            db.execute("UPDATE settings SET value=? WHERE key='enabled'", (json.dumps(body['enabled']),))
            if not body['enabled']:
                db.execute("UPDATE jobs SET status='cancelled', result='功能已禁用' WHERE status IN ('queued','playing')")
            audit(db, 'enable', str(body['enabled']))
        elif action in {'play', 'plan', 'delete_asset'}:
            program = playlist(body, db) if action in {'play', 'plan'} else None
            asset = program['asset'] if program else str(body.get('asset', ''))
            if not (program and program.get('gallery')) and not db.execute('SELECT 1 FROM assets WHERE id=?', (asset,)).fetchone():
                raise ValueError('请先选择已导入的音频')
            if action == 'play':
                if not enabled(db):
                    raise ValueError('请先启用音频播放')
                if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','playing')").fetchone():
                    raise ValueError('已有音频等待或正在播放，请先停止')
                enqueue(db, asset, None, now, program)
            elif action == 'plan':
                if db.execute('SELECT count(*) FROM plans').fetchone()[0] >= 100:
                    raise ValueError('最多保存 100 个计划')
                plan = {k: body[k] for k in ('mode','time','date','weekday') if k in body}
                plan.update(program)
                stamp = next_time(plan, now, validate=True)
                ident = uuid.uuid4().hex
                db.execute('INSERT INTO plans VALUES(?,?,?,1)', (ident, json.dumps(plan), stamp))
                audit(db, 'plan_created', ident)
            else:
                for job in db.execute("SELECT * FROM jobs WHERE status IN ('queued','playing')"):
                    if asset in job_program(db, job)['assets'] or asset == job_program(db, job).get('chime_asset'):
                        raise ValueError('请先停止此音频')
                for row in db.execute('SELECT data FROM plans'):
                    plan = json.loads(row[0])
                    if asset in plan.get('assets', [plan['asset']]) or asset == plan.get('chime_asset'):
                        raise ValueError('请先删除引用此音频的计划')
                media.media_path(asset).unlink(missing_ok=True)
                db.execute('DELETE FROM assets WHERE id=?', (asset,))
                audit(db, 'asset_deleted', asset)
        elif action in {'toggle_plan', 'delete_plan'}:
            ident = str(body.get('id', ''))
            row = db.execute('SELECT * FROM plans WHERE id=?', (ident,)).fetchone()
            if not row:
                raise ValueError('计划不存在')
            if action == 'delete_plan':
                db.execute('DELETE FROM plans WHERE id=?', (ident,))
            else:
                if type(body.get('enabled')) is not bool:
                    raise ValueError('开关必须为布尔值')
                stamp = next_time(json.loads(row['data']), now, validate=True) if body['enabled'] else row['next']
                db.execute('UPDATE plans SET enabled=?,next=? WHERE id=?', (body['enabled'], stamp, ident))
            db.execute("UPDATE jobs SET status='cancelled',result='计划已修改' WHERE plan=? AND status IN ('queued','playing')", (ident,))
            audit(db, action, ident)
        else:
            raise ValueError('未知音频管理操作')
    return {'ok': True}


def enqueue(db, asset, plan, now, program=None):
    ident = uuid.uuid4().hex
    db.execute("INSERT INTO jobs VALUES(?,?,?,?,'queued','等待频道检查')", (ident, asset, plan, now))
    db.execute("DELETE FROM jobs WHERE status NOT IN ('queued','playing') AND id NOT IN (SELECT id FROM jobs ORDER BY created DESC LIMIT 200)")
    if program:
        db.execute('INSERT INTO job_playlists VALUES(?,?)', (ident, json.dumps(program, ensure_ascii=False)))
    db.execute('DELETE FROM job_playlists WHERE job NOT IN (SELECT id FROM jobs)')
    audit(db, 'queued', ident)


def recover():
    # No retransmission after crash, including requests queued while the monitor was down.
    with database() as db:
        db.execute("UPDATE jobs SET status='interrupted',result='服务重启，未恢复发射' WHERE status IN ('queued','playing')")
        advance(db, time.time(), recovery=True)
        audit(db, 'restart', '保留未来计划；未完成发射不恢复；错过的计划不补播')


def advance(db, now, recovery=False):
    for row in db.execute('SELECT * FROM plans WHERE enabled=1 AND next<=? ORDER BY next', (now,)).fetchall():
        plan = json.loads(row['data'])
        if not recovery and enabled(db) and now - row['next'] <= 5:
            enqueue(db, plan['asset'], row['id'], now, plan)
        else:
            audit(db, 'skipped_schedule', row['id'] + ': 已过期、禁用或重启；不补播')
        if plan['mode'] == 'once':
            db.execute('UPDATE plans SET enabled=0 WHERE id=?', (row['id'],))
        else:
            db.execute('UPDATE plans SET next=? WHERE id=?', (next_time(plan, now), row['id']))


def claim(blocker='', now=None, busy=''):
    now = time.time() if now is None else now
    with database() as db:
        advance(db, now)
        jobs = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created").fetchall()
        selected = None
        for job in jobs:
            reason = blocker or ('功能已禁用' if not enabled(db) else '') or (busy if not job['plan'] else '')
            if now - job['created'] > 5:
                reason = '请求过期，不补播'
            if selected:
                reason = '已有音频播放，跳过重叠计划'
            if reason:
                db.execute("UPDATE jobs SET status='skipped',result=? WHERE id=?", (reason, job['id']))
                audit(db, 'skipped', job['id'] + ': ' + reason)
            else:
                db.execute("UPDATE jobs SET status='playing',result='正在播放' WHERE id=?", (job['id'],))
                selected = {**dict(job), **job_program(db, job)}
                audit(db, 'playing', job['id'])
        return selected


def is_live(identifier):
    with database() as db:
        row = db.execute('SELECT status FROM jobs WHERE id=?', (identifier,)).fetchone()
        return enabled(db) and row is not None and row[0] == 'playing' and not db.execute('SELECT 1 FROM jobs WHERE id=? AND plan IS NOT NULL AND plan NOT IN (SELECT id FROM plans)',(identifier,)).fetchone()


def finish(identifier, status, result):
    with database() as db:
        db.execute("UPDATE jobs SET status=?,result=? WHERE id=? AND status='playing'", (status, result, identifier))
        audit(db, status, identifier + ': ' + result)


def set_timeline(identifier, phases):
    with database() as db:
        row = db.execute('SELECT data FROM job_playlists WHERE job=?',(identifier,)).fetchone()
        if row:
            program=json.loads(row[0]);program['_progress']={'phases':phases,'index':-1,'started':time.time()}
            db.execute('UPDATE job_playlists SET data=? WHERE job=?',(json.dumps(program),identifier))


def progress(identifier, message):
    with database() as db:
        db.execute("UPDATE jobs SET result=? WHERE id=? AND status='playing'", (message, identifier))

        row = db.execute('SELECT data FROM job_playlists WHERE job=?',(identifier,)).fetchone()
        if row:
            program=json.loads(row[0]);progress=program.get('_progress')
            if progress:
                for index in range(progress['index']+1,len(progress['phases'])):
                    if progress['phases'][index]['label']==message:
                        progress.update(index=index,started=time.time())
                        db.execute('UPDATE job_playlists SET data=? WHERE job=?',(json.dumps(program),identifier))
                        break
