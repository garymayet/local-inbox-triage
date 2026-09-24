<#
    bench-one-email.ps1  -  Mide lo que de verdad importa: cuanto tarda este equipo en
    CLASIFICAR UN CORREO con un modelo concreto, usando el prompt real del agente.

    A diferencia de llama-bench (que mide pp/tg en abstracto y tarda mucho), esto:
      1) levanta llama-server con el modelo,
      2) espera a que este listo (mide el tiempo de carga, que tambien importa),
      3) envia el MISMO prompt de clasificacion que usa triage_agent.py,
      4) mide latencia total y tokens/s de generacion reales,
      5) apaga el servidor.

    Uso:
        powershell -NoProfile -ExecutionPolicy Bypass -File bench-one-email.ps1 `
                   -Model "C:\ProgramData\llm\Qwen3-4B-Q4_K_M.gguf"
#>
param(
    [Parameter(Mandatory = $true)][string]$Model,
    [int]$Port = 8090,
    [int]$Ctx = 4096,
    [int]$Threads = 0,
    [string]$LlamaDir = "C:\Tools\llama.cpp",
    [int]$TimeoutSec = 900
)

# NOTA: el puerto por defecto es 8090, NO 8080. El 8080 lo ocupa el servicio
# persistente LLMChat; si se usara, el health-check respondería el modelo YA
# cargado y estariamos midiendo otro modelo sin darnos cuenta.
$ErrorActionPreference = "Continue"
$server = Join-Path $LlamaDir "llama-server.exe"
if (-not (Test-Path $server)) { Write-Output "ERROR: no encuentro $server"; exit 1 }
if (-not (Test-Path $Model))   { Write-Output "ERROR: no encuentro el modelo $Model"; exit 1 }

$enUso = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($enUso) {
    Write-Output "ERROR: el puerto $Port ya esta ocupado (pid $($enUso.OwningProcess)). Usa -Port con otro valor."
    exit 1
}

$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
if ($Threads -le 0) { $Threads = $cpu.NumberOfLogicalProcessors }
$ramFree = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 2)

Write-Output "==================================================================="
Write-Output " BENCH DE UN CORREO REAL"
Write-Output "==================================================================="
Write-Output ("  Modelo   : " + (Split-Path $Model -Leaf) + "  (" + [math]::Round((Get-Item $Model).Length/1GB,2) + " GB)")
Write-Output ("  RAM libre: " + $ramFree + " GB     Hilos: " + $Threads + "     Contexto: " + $Ctx)
Write-Output ""

# --- arrancar servidor ---
$logOut = Join-Path $env:TEMP "llamasrv_out.log"
$logErr = Join-Path $env:TEMP "llamasrv_err.log"
$args = @("-m", $Model, "-c", $Ctx, "--port", $Port, "-t", $Threads, "--no-warmup")
$sw = [Diagnostics.Stopwatch]::StartNew()
$proc = Start-Process -FilePath $server -ArgumentList $args -PassThru -WindowStyle Hidden `
                      -RedirectStandardOutput $logOut -RedirectStandardError $logErr
Write-Output ("  servidor lanzado (pid " + $proc.Id + "), esperando /health ...")

$ready = $false
while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
    Start-Sleep -Milliseconds 1500
    try {
        $h = Invoke-RestMethod -Uri ("http://127.0.0.1:" + $Port + "/health") -TimeoutSec 5
        if ($h.status -eq "ok") { $ready = $true; break }
    } catch { }
    if ($proc.HasExited) { Write-Output "  el servidor murio. Ultimas lineas:"; Get-Content $logErr -Tail 8 -ErrorAction SilentlyContinue | ForEach-Object { Write-Output ("    " + $_) }; exit 1 }
}
$loadSec = [math]::Round($sw.Elapsed.TotalSeconds, 1)
if (-not $ready) { Write-Output ("  TIMEOUT esperando al servidor tras " + $loadSec + " s"); if (-not $proc.HasExited) { $proc.Kill() }; exit 1 }
Write-Output ("  LISTO en " + $loadSec + " s (tiempo de carga del modelo)")
Write-Output ""

# --- prompt REAL del agente (extraido de triage_agent.py) ---
$system = 'Eres un clasificador de correo corporativo. Respondes SOLO con JSON valido: {"category":"newsletter|notification|client_ops|internal_team|hr_admin|finance|vendor|personal|action_required|ambiguous", "language":"es|en|pt","urgency":"low|medium|high","requires_reply":true|false, "resumen":"Resumen SIEMPRE EN ESPANOL, en un parrafo de 3 a 5 frases", "reasoning":"1-2 frases"} Se honesto; si no reconoces el patron usa "ambiguous".'

$correo = @"
from: pagos@clientea.com
subject: Cargo duplicado en factura #4411 - solicitud de devolucion
body: Buenos dias, revisando el estado de cuenta detectamos que la factura de marzo fue cobrada dos veces. Necesitamos que se procese la devolucion del cargo duplicado a mas tardar el viernes, de lo contrario tendremos que escalar el caso con nuestro area de finanzas. Quedo atento a su confirmacion. Saludos cordiales.
"@

$payload = @{
    model    = "local"
    stream   = $false
    temperature = 0.1
    response_format = @{ type = "json_object" }
    messages = @(
        @{ role = "system"; content = $system },
        @{ role = "user";   content = $correo }
    )
} | ConvertTo-Json -Depth 6

Write-Output "  Enviando la clasificacion del correo de prueba..."
$sw2 = [Diagnostics.Stopwatch]::StartNew()
try {
    $r = Invoke-RestMethod -Uri ("http://127.0.0.1:" + $Port + "/v1/chat/completions") `
                           -Method Post -Body $payload -ContentType "application/json" -TimeoutSec $TimeoutSec
} catch {
    Write-Output ("  FALLO la peticion: " + $_.Exception.Message)
    if (-not $proc.HasExited) { $proc.Kill() }
    exit 1
}
$sw2.Stop()
$lat = [math]::Round($sw2.Elapsed.TotalSeconds, 2)

$content = $r.choices[0].message.content
$ct = $r.usage.completion_tokens
$pt = $r.usage.prompt_tokens
$tps = if ($ct -and $lat -gt 0) { [math]::Round($ct / $lat, 2) } else { 0 }

Write-Output ""
Write-Output "==================================================================="
Write-Output " RESULTADO"
Write-Output "==================================================================="
Write-Output ("  Carga del modelo : " + $loadSec + " s")
Write-Output ("  Latencia total   : " + $lat + " s   (prompt " + $pt + " tok + salida " + $ct + " tok)")
Write-Output ("  Generacion       : " + $tps + " tok/s")
Write-Output ""
Write-Output "  --- respuesta del modelo ---"
Write-Output ("  " + ($content -replace "`r?`n", " "))
Write-Output ""
try {
    $j = $content | ConvertFrom-Json
    Write-Output "  --- JSON parseado correctamente ---"
    Write-Output ("    category       : " + $j.category)
    Write-Output ("    urgency        : " + $j.urgency)
    Write-Output ("    requires_reply : " + $j.requires_reply)
    Write-Output ("    language       : " + $j.language)
    Write-Output ("    resumen        : " + ($j.resumen -replace "`r?`n"," "))
} catch {
    Write-Output "  ATENCION: el modelo NO devolvio JSON valido -> el agente usaria 'ambiguous'."
    Write-Output "           (criterio de descarte importante para elegir modelo)"
}

if (-not $proc.HasExited) { $proc.Kill() }
Write-Output ""
Write-Output "  servidor detenido."
