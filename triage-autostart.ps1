# triage-autostart.ps1  —  Arranca Ollama (en WSL) + el bridge de triage al iniciar sesion.
#
# Uso: coloca un acceso directo (o un .vbs, ver README) a este script en tu carpeta de Inicio:
#   shell:startup   (pegalo en la barra de direcciones del Explorador)
#
# Ajusta estas 3 rutas a las tuyas:
$proj  = "$env:USERPROFILE\local-inbox-triage"                       # donde clonaste el repo
$py    = "$env:USERPROFILE\triage-venv\Scripts\python.exe"           # python del venv de Windows
$distro = ""   # si tienes VARIAS distros WSL, pon el nombre exacto (wsl -l -v); si solo una, dejalo vacio

# --- 1) Ollama dentro de WSL (proceso propio, oculto; mantiene WSL encendido) ---
$wslArgs = @()
if ($distro -ne "") { $wslArgs += @("-d", $distro) }
$wslArgs += @("--", "bash", "-lc",
  'export OLLAMA_HOST=127.0.0.1:11434 OLLAMA_CONTEXT_LENGTH=8192 OLLAMA_KEEP_ALIVE=30m; exec $HOME/.local/bin/ollama serve >> $HOME/.ollama-serve.log 2>&1')
Start-Process -WindowStyle Hidden -FilePath "wsl.exe" -ArgumentList $wslArgs

# --- 2) Esperar a que Ollama responda (hasta ~60s) ---
for ($i = 0; $i -lt 30; $i++) {
    try { Invoke-WebRequest -UseBasicParsing http://localhost:11434/api/version -TimeoutSec 2 | Out-Null; break }
    catch { Start-Sleep -Seconds 2 }
}

# --- 3) El bridge (python del venv), oculto, con logs a ~/.triage ---
$logs = "$env:USERPROFILE\.triage"
New-Item -ItemType Directory -Force -Path $logs | Out-Null
Start-Process -WindowStyle Hidden -FilePath $py `
  -ArgumentList @("-u", "$proj\triage_agent.py", "--bridge", "--batch", "5") `
  -RedirectStandardOutput "$logs\bridge.log" `
  -RedirectStandardError  "$logs\bridge.err"
