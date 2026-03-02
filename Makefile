all:
	hugo
	mv _site/README.md README.md

clean:
	rm -rf _site
