@echo off
rem === GPU Workbench + Cursor SSH tunnel (run on the school PC) ===
rem Forwards (reachable via Tailscale / campus LAN):
rem   0.0.0.0:8787  -> inspur workbench (127.0.0.1:8787)
rem   0.0.0.0:15000 -> inspur sshd     (127.0.0.1:50000)
rem Auto-reconnects every 5s when the SSH link drops.
rem Install: shell:startup -> create shortcut to this file, or register a
rem           Task Scheduler task "at log on" pointing here.
rem Windows Firewall: allow inbound TCP 8787 and 15000.
:loop
ssh -N -L 0.0.0.0:8787:127.0.0.1:8787 -L 0.0.0.0:15000:127.0.0.1:50000 inspur
echo [%date% %time%] tunnel dropped, retrying in 5s...
timeout /t 5 /nobreak >nul
goto loop
