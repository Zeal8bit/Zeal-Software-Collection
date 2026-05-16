all:
	python3 assets/scripts/generate_category_pages.py
	hugo
	mv _site/README.md README.md

dev:
	python3 assets/scripts/generate_category_pages.py
	hugo server --config hugo.toml,hugo.dev.toml

clean:
	rm -rf _site
