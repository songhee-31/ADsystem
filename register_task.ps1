# 매일 아침 07:30에 run_daily.bat 을 실행하는 Windows 작업 스케줄러 등록
# 사용: 이 폴더에서 PowerShell 로  .\register_task.ps1   (시간 바꾸려면 -At "08:00")
param([string]$At = "07:30")
$bat = Join-Path $PSScriptRoot "run_daily.bat"
schtasks /Create /TN "NaverSA_ChangeImpact" /TR "`"$bat`"" /SC DAILY /ST $At /F
Write-Host "등록 완료: 매일 $At 에 $bat 실행 (PC가 켜져 있어야 합니다)"
