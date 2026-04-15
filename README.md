# Dashboard Financeiro - Contratos Cancelados com Comodato Ativo

Dashboard para acompanhamento de contratos cancelados que ainda possuem equipamentos em comodato.

## Instalação

```bash
# Clone o repositório
git clone git@github.com:ahlrodrigues/dashboard_financeir.git
cd dashboard_financeiro

# Edite as credenciais no install.sh.template
nano install.sh.template
# Altere: SGP_PASS="SUA_SENHA_AQUI"

# Execute a instalação
sudo bash install.sh.template
```

## Estrutura

```
dashboard_financeiro/
├── dashboard_financeiro.html   # Interface do dashboard
├── install.sh.template        # Script de instalação (configure credenciais)
├── uninstall.sh              # Script de desinstalação
└── README.md
```

## Uso

1. Acesse: `http://IP-DO-SERVIDOR:8000/`
2. Selecione o mês desejado
3. Clique em "Atualizar" para recarregar os dados

## Configuração

No arquivo `install.sh.template`, configure:

```bash
SGP_BASE="https://sgp.net4you.com.br/api"
SGP_USER="robo"
SGP_PASS="SUA_SENHA_AQUI"
PORT=8000
```

## Comandos

```bash
# Status do serviço
sudo systemctl status dashboard-financeiro

# Ver logs
sudo journalctl -u dashboard-financeiro -f

# Reiniciar serviço
sudo systemctl restart dashboard-financeiro

# Desinstalar
sudo bash uninstall.sh
```

## Requisitos

- Debian/Ubuntu
- Python 3.8+
- Acesso à internet (para API do SGP)

## Atualização Automática

O dashboard atualiza automaticamente a cada 5 minutos via JavaScript no navegador, usando localStorage para cache.
