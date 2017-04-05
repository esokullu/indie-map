"""MRJob over the Common Crawl WARC dataset that extracts IndieWeb pages.

We consider a page part of the IndieWeb if it has a microformats2 class or
advertizes a webmention or micropub endpoint.

http://commoncrawl.org/
https://github.com/commoncrawl/cc-mrjob
https://pythonhosted.org/mrjob/

https://indieweb.org/
http://microformats.org/wiki/microformats2
https://webmention.net/
https://micropub.net/
"""
import base64
from cStringIO import StringIO
import gzip
import re
import sys
import urlparse

from mrjob.protocol import RawProtocol,  RawValueProtocol
import warc

from mrcc import CCJob


# note that mf2 class names are case sensitive
# http://microformats.org/wiki/parsing-microformats#Parsing_class_values
#
# (also note that this will find false positives when it matches text outside an
# HTML tag.
#
# mf2 h- vocabularies extracted from:
# http://microformats.org/wiki/h-entry#Core_Properties
MF2_CLASSES = ('adr', 'card', 'entry', 'event', 'feed', 'geo', 'item', 'listing', 'product', 'recipe', 'resume', 'review', 'review-aggregate')
MF2_CLASS_RE = re.compile(r"""
class\s*=\s*["'][^"']*\bh-(%s)\b[^"']*["']
""" % '|'.join(MF2_CLASSES), re.VERBOSE | re.UNICODE)


class ExtractIndieweb(CCJob):
  INTERNAL_PROTOCOL = RawProtocol
  OUTPUT_PROTOCOL = RawProtocol

  def process_record(self, record):
    if record['WARC-Type'] != 'response':
      return

    # The HTTP response is defined by a specification: first part is headers
    # (metadata) and then following two CRLFs (newlines) has the response
    payload = record.payload.read()

    http_headers, body = payload.split('\r\n\r\n', 1)
    if 'Content-Type: text/html' in http_headers and body.strip():
      if MF2_CLASS_RE.search(body):
        warcstr = StringIO()
        warcfile = warc.WARCFile(fileobj=warcstr, mode='w')
        warcfile.write_record(warc.WARCRecord(payload=payload, header=record.header))
        warcbuf = base64.b64encode(warcstr.getvalue())
        warcfile.close()

        domain = urlparse.urlparse(record['WARC-Target-URI']).netloc
        # domain = headers['Host']
        yield domain, warcbuf

  def combiner(self, key, values):
    yield key, base64.b64encode(''.join(base64.b64decode(v) for v in values))

  def reducer(self, key, values):
    with gzip.open(u'/tmp/%s.warc.gz' % key, 'w') as out:
      for value in values:
        value = base64.b64decode(value)
        out.write(value)


if __name__ == '__main__':
  ExtractMf2.run()
