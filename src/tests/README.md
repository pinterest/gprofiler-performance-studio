## Build the test container
```sh
docker build -t gprofiler-test .
```

## Run the test container
```sh
docker run --rm -t --network host --env-file .env gprofiler-test python run_tests.py --test-path integration/
```