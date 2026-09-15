# -*- coding: utf-8 -*-
"""
HTTP-shim met requests-achtige API bovenop urllib2
==================================================

`requests` werkt NIET in de pyRevit IronPython 2.7-engine. urllib3 zet op de
SSL-context de opties OP_NO_SSLv2 | OP_NO_SSLv3 | OP_NO_COMPRESSION |
OP_NO_TICKET (urllib3/util/ssl_.py:309-319); de ssl-shim van IronPython kent
die constanten niet en laat ze bij het protocolnummer terechtkomen. Het
resultaat is altijd dezelfde crash in do_handshake:

    SystemError: bad ssl protocol type: 50479106
                 (= 2 + 0x1000000 + 0x2000000 + 0x20000 + 0x4000)

De rest van gis2bim.api gebruikt daarom rechtstreeks urllib2. Deze module
biedt dezelfde aanroepen als `requests` voor code die er al op geschreven was,
zodat die niet regel voor regel om hoeft:

    from gis2bim.api import http_compat as requests
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    data = response.json()

Ondersteund: get(), post(json=..., headers=...), Response.json/.text/
.content/.status_code/.ok/.raise_for_status(), exceptions.RequestException.
Bewust NIET ondersteund: sessions, cookies, redirect-config, streaming,
auth, proxies. Heb je die nodig, schrijf dan direct urllib2.
"""

import json as _json

# Forceer TLS 1.2 voor PDOK (vereist op IronPython/.NET)
try:
    import clr
    clr.AddReference("System")
    from System.Net import ServicePointManager, SecurityProtocolType
    ServicePointManager.SecurityProtocol = (
        SecurityProtocolType.Tls12 | SecurityProtocolType.Tls11
    )
except Exception:
    pass

try:
    import urllib2
    from urllib2 import HTTPError, URLError
except ImportError:
    # Python 3
    import urllib.request as urllib2
    from urllib.error import HTTPError, URLError


USER_AGENT = "GIS2BIM-pyRevit/1.0"

# Tekens die niet onverpakt in een URL mogen. `requests` codeert deze zelf,
# urllib2 niet - een adresquery als "Den Haag and Zeekant" sneuvelt daarop.
# `%` staat er bewust niet bij: dat zou al gecodeerde tekens dubbel coderen.
_UNSAFE = u' "<>{}|\\^`'


class RequestException(Exception):
    """Basisfout, spiegelt requests.exceptions.RequestException."""


class HTTPStatusError(RequestException):
    """Niet-2xx status, spiegelt requests.exceptions.HTTPError."""


class _Exceptions(object):
    """Naamruimte zodat `requests.exceptions.RequestException` blijft werken."""
    RequestException = RequestException
    HTTPError = HTTPStatusError
    ConnectionError = RequestException
    Timeout = RequestException


exceptions = _Exceptions()


try:
    unicode_type = unicode  # noqa: F821  (Python 2 / IronPython)
except NameError:
    unicode_type = str


def _sanitize_url(url):
    """Percent-codeer tekens die urllib2 niet accepteert."""
    if not isinstance(url, unicode_type):
        try:
            url = url.decode("utf-8")
        except Exception:
            return url

    out = []
    for ch in url:
        if ch in _UNSAFE or ord(ch) < 0x21 or ord(ch) > 0x7E:
            for byte in ch.encode("utf-8"):
                # IronPython 2.7: iteratie over str geeft tekens, niet ints
                value = byte if isinstance(byte, int) else ord(byte)
                out.append("%%%02X" % value)
        else:
            out.append(ch)
    return "".join(out)


class Response(object):
    """Minimale requests.Response-vervanger."""

    def __init__(self, status_code, content, url, headers=None):
        self.status_code = status_code
        self.content = content
        self.url = url
        self.headers = headers or {}

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    @property
    def text(self):
        if isinstance(self.content, bytes):
            return self.content.decode("utf-8", "replace")
        return self.content

    def json(self):
        return _json.loads(self.text)

    def raise_for_status(self):
        if not self.ok:
            raise HTTPStatusError(
                "HTTP {0} voor {1}".format(self.status_code, self.url))


def _send(url, data=None, headers=None, timeout=30):
    url = _sanitize_url(url)

    request = urllib2.Request(url, data=data)
    request.add_header("User-Agent", USER_AGENT)
    for key, value in (headers or {}).items():
        request.add_header(key, value)

    try:
        response = urllib2.urlopen(request, timeout=timeout)
        return Response(
            getattr(response, "code", 200), response.read(), url,
            dict(response.info().items()))
    except HTTPError as e:
        # Spiegelt requests: geen exception, maar een Response met de status.
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        return Response(e.code, body, url)
    except URLError as e:
        raise RequestException("Verbinding mislukt voor {0}: {1}".format(
            url, getattr(e, "reason", e)))
    except Exception as e:
        raise RequestException("Request mislukt voor {0}: {1}".format(url, e))


def get(url, timeout=30, headers=None, **kwargs):
    """GET-request. `params` wordt niet ondersteund; bouw de querystring zelf."""
    if kwargs.get("params"):
        raise RequestException(
            "http_compat.get() ondersteunt geen params=; zet de querystring "
            "in de URL")
    return _send(url, data=None, headers=headers, timeout=timeout)


def post(url, data=None, json=None, headers=None, timeout=30, **kwargs):
    """POST-request. `json=` serialiseert en zet de Content-Type header."""
    headers = dict(headers or {})

    if json is not None:
        body = _json.dumps(json)
        headers.setdefault("Content-Type", "application/json")
    elif isinstance(data, (dict, list)):
        body = _json.dumps(data)
        headers.setdefault("Content-Type", "application/json")
    else:
        body = data

    if body is not None and not isinstance(body, bytes):
        body = body.encode("utf-8")

    return _send(url, data=body, headers=headers, timeout=timeout)
