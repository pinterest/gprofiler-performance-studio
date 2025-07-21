## Run tests using only local services
```sh
docker-compose --profile with-clickhouse --profile with-postgres --profile with-webapp --profile with-ch-rest-service --profile with-agents-logs-backend --profile with-periodic-tasks --profile with-ch-indexer --profile with-nginx-load-balancer up -d --build && docker attach gprofiler-tester
```

## Stop containers and remove volumes
```sh
docker-compose --profile with-clickhouse --profile with-postgres --profile with-webapp --profile with-ch-rest-service --profile with-agents-logs-backend --profile with-periodic-tasks --profile with-ch-indexer --profile with-nginx-load-balancer down -v --remove-orphans
```