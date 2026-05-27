.PHONY: build run demo check clean coral-register setup

build:
	./build.sh

run:
	./run.sh

demo:
	./dev.sh

check:
	./scripts/check.sh

clean:
	rm -rf backend/.venv frontend/node_modules frontend/dist backend/__pycache__ __pycache__ .DS_Store backend/.DS_Store

coral-register:
	./scripts/register_coral_sources.sh

setup:
	./setup.sh
