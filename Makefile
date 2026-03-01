all:
	pnpm i
	pnpm run build
	mv _site/README.md README.md
