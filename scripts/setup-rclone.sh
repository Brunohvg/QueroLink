#!/bin/bash
# ============================================================
# QueroLink — setup-rclone.sh
# Guia de configuracao unica do rclone com Google Drive
# Execute UMA VEZ no servidor, dentro do container web:
#   docker exec -it querolink-web bash
#   ./scripts/setup-rclone.sh
# ============================================================

echo "============================================"
echo " SETUP RCLONE — GOOGLE DRIVE (execute 1 vez) "
echo "============================================"
echo ""
echo "Siga as instrucoes:"
echo ""
echo "1. Quando aparecer 'name>', digite: gdrive"
echo "2. Quando aparecer 'Storage>', digite: 18 (drive)"
echo "3. client_id:     aperte ENTER (default)"
echo "4. client_secret: aperte ENTER (default)"
echo "5. scope:         digite 1 (full access)"
echo "6. root_folder_id: aperte ENTER"
echo "7. service_account_file: aperte ENTER"
echo "8. Edit advanced config:  n"
echo "9. Use web browser to authenticate: n"
echo "10. Copie a URL exibida, abra no SEU PC, autentique com Google"
echo "11. Cole o token/verification code de volta no terminal"
echo "12. Configure this as a Shared Drive: n"
echo "13. y) Yes this is OK"
echo "14. q) Quit config"
echo ""
echo "Pressione ENTER para continuar..."
read -r

rclone config

echo ""
echo "============================================"
echo " Verificando conexao com Google Drive...    "
echo "============================================"
rclone ls gdrive: 2>&1 | head -5

echo ""
echo "============================================"
echo " Criando pasta querolink-backups no Drive... "
echo "============================================"
rclone mkdir gdrive:querolink-backups 2>/dev/null || true

echo ""
echo "===== SETUP CONCLUIDO ====="
echo "Teste manual: rclone ls gdrive:querolink-backups/"
