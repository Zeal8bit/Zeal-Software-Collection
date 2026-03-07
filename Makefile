all:
	hugo
	mv _site/README.md README.md

dev:
	hugo server --config hugo.toml,hugo.dev.toml

clean:
	rm -rf _site
