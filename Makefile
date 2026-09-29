.PHONY: test build check demo all

all: test build

test:
	python3 -m unittest discover -s tests -t .

build:
	python3 build.py

check:
	python3 build.py --check

demo:
	python3 demo.py
