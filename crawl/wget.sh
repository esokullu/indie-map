#!/bin/bash

wget --recursive --level=inf --no-check-certificate --timeout=120 --no-verbose \
    --adjust-extension --convert-links --wait=.3 --random-wait --tries=5 \
    --follow-tags=a \
    --reject="avi,bz2,bzip,bzip2,css,csv,doc,docx,exe,gif,GIF,gz,GZ,gzip,ico,iso,jar,jpeg,JPEG,jpg,JPG,js,json,m4a,mov,mp3,mp4,mpg,odt,ogg,pdf,png,PNG,rar,rdf,svg,swf,SWF,tar,txt,text,wav,wmv,xml,xpi,Z,zip,[?&]_t=amp,[?&]_t=rss" \
    --warc-file=$1 $1 \
    || true