<#
    eval-models.ps1  -  Evaluacion de CALIDAD (no solo velocidad) de modelos locales.

    Pasa un conjunto de correos sinteticos (es/en/pt) con el prompt EXACTO del agente y
    puntua, por modelo:
      - validez del JSON (el agente hace json.loads directo: si el modelo envuelve en ```json, falla)
      - acierto de categoria, admitiendo un CONJUNTO de etiquetas validas por caso
        (la taxonomia del agente se solapa: finance/client_ops, hr_admin/notification...)
      - latencia media y tokens de salida

    Por que existe: la comparativa de un solo correo (compare-models.ps1) es n=1 y no permite
    decidir. Esto da una senal con 8 casos sin tocar tu buzon.

    Uso:
        powershell -NoProfile -ExecutionPolicy Bypass -File eval-models.ps1
        powershell ... -File eval-models.ps1 -Models "Qwen2.5-3B-Instruct-Q4_K_M.gguf","gemma-3-4b-it-Q4_K_M.gguf"

    Usa el puerto 8090 para NO tocar el servicio LLMChat. Solo lectura.
#>
param(
    [string[]]$Models = @(
        "Qwen2.5-3B-Instruct-Q4_K_M.gguf",
        "gemma-3-4b-it-Q4_K_M.gguf"
    ),
    [string]$ModelsDir = "C:\ProgramData\llm",
    [string]$LlamaDir = "C:\Tools\llama.cpp",
    [int]$Port = 8090,
    [int]$Threads = 0,
    [int]$TimeoutSec = 900
)

$ErrorActionPreference = "Continue"
$server = Join-Path $LlamaDir "llama-server.exe"
if (-not (Test-Path $server)) { Write-Output "ERROR: falta $server"; exit 1 }
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    Write-Output "ERROR: puerto $Port ocupado"; exit 1
}
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
if ($Threads -le 0) { $Threads = $cpu.NumberOfLogicalProcessors }

# --- Prompt REAL del agente (copiado de triage_agent.py) ---
$SYSTEM = 'Eres un clasificador de correo corporativo. Respondes SOLO con JSON valido: {"category":"newsletter|notification|client_ops|internal_team|hr_admin|finance|vendor|personal|action_required|ambiguous", "language":"es|en|pt","urgency":"low|medium|high","requires_reply":true|false, "resumen":"Resumen SIEMPRE EN ESPANOL (aunque el correo este en ingles o portugues), en un parrafo de 3 a 5 frases", "reasoning":"1-2 frases"} Se honesto; si no reconoces el patron usa "ambiguous".'

# --- Casos: 'ok' es el CONJUNTO de categorias aceptables ---
$casos = @(
    @{ id = "factura-cliente-es"; from = "pagos@clientea.com"; subject = "Cargo duplicado factura #4411";
       body = "Buenos dias, la factura de marzo fue cobrada dos veces. Necesitamos la devolucion del cargo duplicado antes del viernes o escalaremos el caso. Saludos.";
       ok = @("finance", "client_ops") },
    @{ id = "newsletter-en"; from = "news@vendor.com"; subject = "March product newsletter";
       body = "Here are this month's updates, new features and upcoming webinars. You can unsubscribe at any time.";
       ok = @("newsletter") },
    @{ id = "aviso-password-pt"; from = "no-reply@sistema.com"; subject = "Sua senha foi alterada";
       body = "Informamos que sua senha foi alterada com sucesso. Se nao foi voce, entre em contato imediatamente.";
       ok = @("notification") },
    @{ id = "rrhh-vacaciones-es"; from = "rrhh@dxc.com"; subject = "Vacaciones pendientes de aprobar";
       body = "Recuerda que tienes 5 dias de vacaciones pendientes de solicitar antes del 31 de diciembre.";
       ok = @("hr_admin", "notification") },
    @{ id = "cotizacion-proveedor-es"; from = "ventas@proveedor.com"; subject = "Cotizacion licencias anuales";
       body = "Adjunto la cotizacion de las 25 licencias anuales que solicito. El precio unitario baja si confirmamos antes de fin de mes.";
       ok = @("vendor", "finance") },
    @{ id = "alerta-monitoreo-en"; from = "alerts@monitoring.internal"; subject = "ALERT: CPU above 95% for 15 min";
       body = "Automated alert: host srv-app-07 CPU utilization has exceeded 95% for 15 minutes. No action required if already known.";
       ok = @("notification") },
    @{ id = "coordinacion-interna-es"; from = "companero@dxc.com"; subject = "Revision del sprint el jueves";
       body = "Hola, movemos la revision del sprint al jueves a las 10? Necesito que confirmes si puedes asistir para cerrar el alcance.";
       ok = @("internal_team", "action_required") },
    @{ id = "personal-en"; from = "john.friend@gmail.com"; subject = "BBQ this weekend?";
       body = "Hey! We are doing a barbecue on Saturday, want to come over? Let me know so I can buy enough food.";
       ok = @("personal") }
)

function Invoke-Caso($port, $caso, $timeoutSec) {
    $body = "from: " + $caso.from + "`nsubject: " + $caso.subject + "`nbody: " + $caso.body
    $payload = @{
        model = "local"; stream = $false; temperature = 0.1
        response_format = @{ type = "json_object" }
        messages = @(@{ role = "system"; content = $SYSTEM }, @{ role = "user"; content = $body })
    } | ConvertTo-Json -Depth 6
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $res = [pscustomobject]@{ lat = $null; jsonOk = $false; cat = "?"; salida = $null; err = "" }
    try {
        $r = Invoke-RestMethod -Uri ("http://127.0.0.1:" + $port + "/v1/chat/completions") -Method Post `
                               -Body $payload -ContentType "application/json" -TimeoutSec $timeoutSec
        $sw.Stop()
        $res.lat = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        $res.salida = $r.usage.completion_tokens
        $c = $r.choices[0].message.content
        try { $j = $c | ConvertFrom-Json; if ($j.category) { $res.jsonOk = $true; $res.cat = $j.category } }
        catch { $res.err = "JSON invalido (el agente usaria 'ambiguous')" }
    } catch {
        $sw.Stop(); $res.lat = [math]::Round($sw.Elapsed.TotalSeconds, 1); $res.err = $_.Exception.Message
    }
    return $res
}

$resumen = @()
foreach ($m in $Models) {
    $ruta = Join-Path $ModelsDir $m
    if (-not (Test-Path $ruta)) { Write-Output ("[skip] no existe " + $m); continue }

    Write-Output ("===================================================================")
    Write-Output (" MODELO: " + $m)
    Write-Output ("===================================================================")
    $logOut = Join-Path $env:TEMP "eval_srv_out.log"
    $logErr = Join-Path $env:TEMP "eval_srv_err.log"
    $proc = Start-Process -FilePath $server -PassThru -WindowStyle Hidden `
        -ArgumentList @("-m", $ruta, "-c", 4096, "--port", $Port, "-t", $Threads, "--no-warmup") `
        -RedirectStandardOutput $logOut -RedirectStandardError $logErr

    $sw = [Diagnostics.Stopwatch]::StartNew(); $listo = $false
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        Start-Sleep -Milliseconds 1500
        try { $h = Invoke-RestMethod ("http://127.0.0.1:" + $Port + "/health") -TimeoutSec 5; if ($h.status -eq "ok") { $listo = $true; break } } catch {}
        if ($proc.HasExited) { break }
    }
    if (-not $listo) {
        Write-Output "  ERROR: el servidor no levanto"
        if (-not $proc.HasExited) { $proc.Kill() }
        continue
    }
    Write-Output ("  modelo cargado en " + [math]::Round($sw.Elapsed.TotalSeconds,1) + " s")

    $aciertos = 0; $jsonOk = 0; $lats = @(); $detalle = @()
    foreach ($c in $casos) {
        $r = Invoke-Caso $Port $c $TimeoutSec
        $okCat = $c.ok -contains $r.cat
        if ($okCat) { $aciertos++ }
        if ($r.jsonOk) { $jsonOk++ }
        if ($r.lat) { $lats += $r.lat }
        $detalle += [pscustomobject]@{ Caso = $c.id; Cat = $r.cat; Esperado = ($c.ok -join "/"); Ok = $okCat; Lat = $r.lat; Salida = $r.salida }
        Write-Output ("    " + $c.id.PadRight(24) + " -> " + $r.cat.PadRight(16) + " (esperado " + ($c.ok -join "/") + ")  " + $(if ($okCat) { "OK" } else { "FALLO" }) + "   " + $r.lat + "s")
    }
    if (-not $proc.HasExited) { $proc.Kill() }
    Start-Sleep -Seconds 3

    $media = 0; if ($lats.Count -gt 0) { $media = [math]::Round(($lats | Measure-Object -Average).Average, 1) }
    $resumen += [pscustomobject]@{
        Modelo = ($m -replace "-Q4_K_M\.gguf", "")
        Categoria = "$aciertos/$($casos.Count)"; Json = "$jsonOk/$($casos.Count)"; LatMedia = $media
    }
    Write-Output ""
}

Write-Output "==================================================================="
Write-Output " RESUMEN DE CALIDAD"
Write-Output "==================================================================="
Write-Output ("Modelo".PadRight(30) + "Aciertos cat".PadLeft(14) + "JSON valido".PadLeft(14) + "Latencia media".PadLeft(16))
Write-Output ("-" * 78)
foreach ($r in ($resumen | Sort-Object { [double]($_.LatMedia) })) {
    Write-Output ($r.Modelo.PadRight(30) + $r.Categoria.PadLeft(14) + $r.Json.PadLeft(14) + ([string]$r.LatMedia + " s").PadLeft(16))
}
Write-Output ""
Write-Output "Criterios de decision, en orden:"
Write-Output "  1) JSON valido 100% (si no, el agente degrada a 'ambiguous' y pierde utilidad)"
Write-Output "  2) mas aciertos de categoria"
Write-Output "  3) menor latencia media"
Write-Output "OJO: muestra pequena (8 casos sinteticos). Para decidir en serio, repetir con 20-30"
Write-Output "correos REALES cuando Outlook este configurado (triage_agent.py --dry-run)."
