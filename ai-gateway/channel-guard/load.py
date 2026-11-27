import subprocess,time
file='/opt/fmo-ai-gateway/channel-guard/fmo_audio_guard.erl'
expr='begin {ok,fmo_audio_guard,B}=compile:file('+str(list(file.encode()))+',[binary]), {module,fmo_audio_guard}=code:load_binary(fmo_audio_guard,'+str(list(file.encode()))+',B), fmo_audio_guard:install() end.'
for i in range(20):
 r=subprocess.run(['/usr/bin/emqx','eval',expr],capture_output=True,text=True)
 if r.returncode==0 and r.stdout.strip()=='ok':print('Audio publish guard installed');break
 if i==19:raise RuntimeError(r.stdout+r.stderr)
 time.sleep(1)
