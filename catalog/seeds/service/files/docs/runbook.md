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
