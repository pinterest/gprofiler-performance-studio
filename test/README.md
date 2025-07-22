## Run tests using only local services
```sh
docker compose --profile with-all up -d --build && docker attach gprofiler-tester
```

## Stop containers and remove volumes
```sh
docker compose --profile with-all down -v
```