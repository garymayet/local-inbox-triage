<#
    install-triage-bridge-task.ps1
    ------------------------------------------------------------------------
    Registra el bridge de triaje como tarea de PRODUCCION en el Programador de
    tareas de Windows.

    Por que una tarea y no la carpeta de Inicio:
      - La carpeta de Inicio lanza el proceso cuando el usuario entra, pero si
        el proceso muere nadie lo levanta. La tarea tiene RestartOnFailure.
      - El COM de Outlook SOLO funciona en la sesion interactiva del usuario
        que tiene el perfil -> LogonType=InteractiveToken (sin contrasena).

    Diferencia con run-in-user-session.ps1 (que es una herramienta de un disparo):
      - Esta tarea es permanente, sin limite de tiempo (PT0S) y con reinicio
        automatico. run-in-user-session.ps1 usa PT1H a proposito porque solo
        ejecuta pruebas cortas.

    Idempotente: se puede ejecutar tantas veces como haga falta; sobreescribe la
    tarea existente. Verifica el resultado leyendo la definicion registrada.

    Uso (desde Run Command / SYSTEM):
        powershell -ExecutionPolicy Bypass -File install-triage-bridge-task.ps1
        powershell -ExecutionPolicy Bypass -File install-triage-bridge-task.ps1 -Start
        powershell -ExecutionPolicy Bypass -File install-triage-bridge-task.ps1 -Uninstall
#>
param(
    # SID de mmayet (cuenta Entra). Los nombres de Entra NO se resuelven desde
    # SYSTEM en muchas APIs; el SID si. Se puede sobreescribir.
    [string]$SID = "S-1-12-1-373113246-1305811427-3182305448-1525127871",
    [string]$UserHome = "C:\Users\mmayet",
    [string]$Repo = "C:\Users\mmayet\local-inbox-triage",
    [string]$TaskName = "TriageBridge",
    [int]$Batch = 5,
    [int]$Interval = 20,
    [switch]$Start,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$svc = New-Object -ComObject Schedule.Service
$svc.Connect()
$root = $svc.GetFolder("\")

if ($Uninstall) {
    try { $root.DeleteTask($TaskName, 0); Write-Output "tarea '$TaskName' eliminada" }
    catch { Write-Output ("no se pudo eliminar: " + $_.Exception.Message) }
    return
}

$py      = Join-Path $Repo "triage-venv\Scripts\python.exe"
$agent   = Join-Path $Repo "triage_agent.py"
$stateDir = Join-Path $UserHome ".triage"
$log     = Join-Path $stateDir "bridge.log"

foreach ($p in @($py, $agent)) {
    if (-not (Test-Path $p)) { throw "no existe: $p" }
}
if (-not (Test-Path $stateDir)) { New-Item -ItemType Directory -Force -Path $stateDir | Out-Null }

# -u  -> stdout sin buffer: el log se ve EN VIVO (si no, aparece de golpe al morir el proceso)
# >>  -> conserva el historial entre reinicios (no trunca); util para ver por que murio
$argLine = '/c ""' + $py + '" -u "' + $agent + '" --bridge --batch ' + $Batch +
           ' --interval ' + $Interval + ' >> "' + $log + '" 2>&1"'

$def = $svc.NewTask(0)
$def.RegistrationInfo.Description = "Bridge de triaje de inbox (OneDrive <-> Power Automate/Teams). Corre en la sesion del usuario porque el COM de Outlook lo exige."
$def.RegistrationInfo.Author = "DSH deploy"
$def.Settings.Enabled = $true
$def.Settings.ExecutionTimeLimit = "PT0S"          # <-- SIN limite (era PT1H y mataba el bridge cada hora)
$def.Settings.StartWhenAvailable = $true
$def.Settings.DisallowStartIfOnBatteries = $false
$def.Settings.StopIfGoingOnBatteries = $false
$def.Settings.AllowHardTerminate = $true
$def.Settings.MultipleInstances = 2                 # TASK_INSTANCES_IGNORE_NEW
$def.Settings.RestartCount = 3
$def.Settings.RestartInterval = "PT1M"
$def.Principal.LogonType = 3                        # TASK_LOGON_INTERACTIVE_TOKEN (sin contrasena)
$def.Principal.RunLevel = 0                         # sin elevar

$action = $def.Actions.Create(0)                    # TASK_ACTION_EXEC
$action.Path = "C:\Windows\System32\cmd.exe"
$action.Arguments = $argLine
$action.WorkingDirectory = $Repo

$trigger = $def.Triggers.Create(9)                  # TASK_TRIGGER_LOGON
$trigger.Enabled = $true
$trigger.UserId = $SID

$registrado = $false
foreach ($u in @($SID, "mmayet")) {
    try {
        $def.Principal.UserId = $u
        $trigger.UserId = $u
        $root.RegisterTaskDefinition($TaskName, $def, 6, $null, $null, 3) | Out-Null
        Write-Output ("  registrada con UserId='" + $u + "'")
        $registrado = $true
        break
    } catch {
        Write-Output ("  UserId='" + $u + "' fallo: " + $_.Exception.Message)
    }
}
if (-not $registrado) { throw "no pude registrar la tarea '$TaskName'" }

# ---- Verificacion: releer lo que quedo REALMENTE registrado (no dar por hecho) ----
$t = $root.GetTask("\" + $TaskName)
Write-Output ""
Write-Output "=== VERIFICACION (leido de la tarea registrada) ==="
Write-Output ("  State              : " + $t.State)
Write-Output ("  ExecutionTimeLimit : " + $t.Definition.Settings.ExecutionTimeLimit)
Write-Output ("  LogonType          : " + $t.Definition.Principal.LogonType + "   (3 = InteractiveToken)")
Write-Output ("  RunLevel           : " + $t.Definition.Principal.RunLevel)
Write-Output ("  UserId             : " + $t.Definition.Principal.UserId)
Write-Output ("  RestartCount       : " + $t.Definition.Settings.RestartCount + " cada " + $t.Definition.Settings.RestartInterval)
Write-Output ("  Triggers           : " + $t.Definition.Triggers.Count)
foreach ($tr in $t.Definition.Triggers) { Write-Output ("      tipo=" + $tr.Type + " user=" + $tr.UserId + " enabled=" + $tr.Enabled) }
foreach ($a in $t.Definition.Actions) { Write-Output ("  Action             : " + $a.Path + " " + $a.Arguments) }
Write-Output ("  Log                : " + $log)

if ($t.Definition.Settings.ExecutionTimeLimit -ne "PT0S") {
    Write-Warning "OJO: ExecutionTimeLimit no quedo en PT0S -> el bridge morira por limite de tiempo."
}

if ($Start) {
    $root.GetTask("\" + $TaskName).Run($null) | Out-Null
    Write-Output "  tarea LANZADA"
}
