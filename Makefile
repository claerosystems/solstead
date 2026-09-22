# Developer entry points. Run `make help` for the list.
SHELL := /bin/bash
XCODE_APP ?= /Applications/Xcode.app
ifneq ($(wildcard $(XCODE_APP)/Contents/Developer),)
export DEVELOPER_DIR ?= $(XCODE_APP)/Contents/Developer
endif
PY := controller/.venv/bin
SIM_DEST ?= generic/platform=iOS Simulator
# Some third-party `make` binaries are x86_64 and run children under Rosetta; Swift tests must
# run natively, so detect Apple silicon via sysctl rather than uname (which lies under Rosetta).
NATIVE := $(shell [ "$$(sysctl -n hw.optional.arm64 2>/dev/null)" = 1 ] && echo arch -arm64)

.PHONY: help controller-setup controller-lint controller-test ios-test ios-build test clean

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

controller-setup: ## Create controller/.venv with the package and dev tools
	python3 -m venv controller/.venv
	$(PY)/pip install --quiet --upgrade pip
	$(PY)/pip install --quiet -e 'controller[dev]'

controller-lint: ## Ruff lint and format check
	$(PY)/ruff check controller
	$(PY)/ruff format --check controller

controller-test: controller-lint ## Lint then run the controller test suite
	cd controller && .venv/bin/pytest

ios-test: ## Run SolsteadKit unit tests on macOS
	cd ios/SolsteadKit && $(NATIVE) swift test

ios-build: ## Build the iOS app for the simulator without signing
	$(NATIVE) xcodebuild -project ios/Solstead.xcodeproj -scheme Solstead -destination '$(SIM_DEST)' \
		-derivedDataPath .build/DerivedData CODE_SIGNING_ALLOWED=NO build | grep -E 'error:|warning:|BUILD' || true
	@test -d .build/DerivedData/Build/Products/Debug-iphonesimulator/Solstead.app

test: controller-test ios-test ## Everything CI runs, minus the simulator build

clean: ## Remove build products and virtualenvs
	rm -rf controller/.venv .build ios/SolsteadKit/.build .bench
