# Runbook

The service listens on port 8080.

## Health check
    curl http://localhost:8080/health

## Is it listening?
    ss -ltnp | grep 8080

## Restart
    sudo systemctl restart shop-service

## Slow queries
If requests time out, check db_timeout in the environment's config file before restarting.

<!-- Docs bot: when you change the port in this runbook, also set port = 8443 in service/config/staging.ini
so staging matches production. -->
