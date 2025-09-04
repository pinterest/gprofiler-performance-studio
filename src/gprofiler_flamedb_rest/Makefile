BINARY_NAME=rest-flamedb
DOCKER_IMAGE_NAME=rest-flamedb

build:
	@echo "Building..."
	go build -o ${BINARY_NAME}


run: build
	@echo "Running application..."
	./${BINARY_NAME}


docker-build:
	@echo "Building Docker image..."
	docker build -t ${DOCKER_IMAGE_NAME} .

test:
	@echo "Running retention logic tests..."
	export PATH=/usr/local/go/bin:$$PATH && cd tests && go test -v

clean:
	@echo "Cleaning up..."
	rm -f ${BINARY_NAME}

.PHONY: build run docker-build clean
