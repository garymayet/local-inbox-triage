<#
    run-in-user-session.ps1  -  Ejecuta un comando en la SESION INTERACTIVA del usuario
    mmayet y devuelve su salida.

    Por que hace falta: el COM de Outlook solo funciona en la sesion del usuario que tiene el
    perfil. El agente corre aqui como SYSTEM (Run Command), asi que win32com no puede hablar
    con Outlook. Esta tarea programada se registra con LogonType=InteractiveToken, que NO
    necesita la contrasena del usuario: usa el token de su sesion ya iniciada.

    Uso:
        powershell -File run-in-user-session.ps1 -AgentArgs "triage_agent.py --folders" -Tag folders
#>
param(
    [string]$AgentArgs = "",
    [string]$Exe = "",
    [string]$Tag = "run",
    [int]$EsperaSeg = 180,
    [string]$Repo = "C:\Users\mmayet\local-inbox-triage"
)

# Si se indica -Exe se lanza ESE ejecutable (p.ej. OneDrive para el asistente de sesion);
# si no, se ejecuta el python del venv del agente con -Args.
$SID = "S-1-12-1-373113246-1305811427-3182305448-1525127871"   # mmayet (Entra)
if ($Exe) {
    $target = $Exe
    $cmdArgs = '/c start "" "' + $Exe + '" ' + $AgentArgs
    $log = Join-Path $Repo ("usersession-$Tag.log")
} else {
    $target = Join-Path $Repo "triage-venv\Scripts\python.exe"
    $log = Join-Path $Repo ("usersession-$Tag.log")
    if (Test-Path $log) { Remove-Item $log -Force -ErrorAction SilentlyContinue }
    $cmdArgs = '/c ""' + $target + '" ' + $AgentArgs + ' > "' + $log + '" 2>&1"'
}
$tn = "TriageUserRun"

$svc = New-Object -ComObject Schedule.Service
$svc.Connect()
$root = $svc.GetFolder("\")

$def = $svc.NewTask(0)
$def.RegistrationInfo.Description = "Ejecuta el triaje en la sesion del usuario (COM de Outlook)"
$def.Settings.Enabled = $true
$def.Settings.ExecutionTimeLimit = "PT1H"
$def.Settings.StartWhenAvailable = $true
$def.Settings.DisallowStartIfOnBatteries = $false
$def.Settings.StopIfGoingOnBatteries = $false
$def.Principal.LogonType = 3   # TASK_LOGON_INTERACTIVE_TOKEN (sin contrasena)
$def.Principal.RunLevel = 0    # sin elevar

$action = $def.Actions.Create(0)   # TASK_ACTION_EXEC
$action.Path = "C:\Windows\System32\cmd.exe"
$action.Arguments = $cmdArgs
$action.WorkingDirectory = $Repo

$registrado = $false
$usados = @($SID, "mmayet", "mitchell.mayet@dxc.com")
foreach ($u in $usados) {
    try {
        $def.Principal.UserId = $u
        $root.RegisterTaskDefinition($tn, $def, 6, $null, $null, 3) | Out-Null
        Write-Output ("  tarea registrada con UserId='" + $u + "'")
        $registrado = $true
        break
    } catch {
        Write-Output ("  UserId='" + $u + "' fallo: " + $_.Exception.Message)
    }
}
if (-not $registrado) { Write-Output "ERROR: no pude registrar la tarea"; exit 1 }

try {
    $root.GetTask("\$tn").Run($null) | Out-Null
    Write-Output "  tarea lanzada; esperando salida..."
} catch {
    Write-Output ("ERROR al lanzar: " + $_.Exception.Message); exit 1
}

$t0 = Get-Date
while (((Get-Date) - $t0).TotalSeconds -lt $EsperaSeg) {
    Start-Sleep -Seconds 3
    $t = $root.GetTask("\$tn")
    $estado = $t.State   # 4 = running, 3 = ready
    if ($estado -ne 4 -and (Test-Path $log)) {
        Start-Sleep -Seconds 2
        break
    }
}

Write-Output "=== SALIDA DE LA SESION DEL USUARIO ==="
if (Test-Path $log) {
    $c = Get-Content $log -ErrorAction SilentlyContinue
    Write-Output ("  lineas: " + $c.Count)
    $c | ForEach-Object { Write-Output ("  " + ($_ -replace "[^\x20-\x7E]", "")) }
} else {
    Write-Output "  no se genero el log (la tarea no llego a ejecutarse en la sesion?)"
}
$t = $root.GetTask("\$tn")
Write-Output ("  estado final de la tarea: " + $t.State + "  ultimo resultado: " + $t.LastTaskResult)
