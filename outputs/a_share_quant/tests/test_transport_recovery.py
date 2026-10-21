import json
from unittest.mock import patch

import pytest

from quant.data.extension_transport import ProviderError, query


class Response:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self, size=-1):return json.dumps({'code':0,'data':{'fields':['ts_code'],'items':[['x']]}}).encode()

def test_connection_reset_is_bounded_and_retries_without_logging_provider_text():
    with patch('quant.data.extension_transport.limited_tushare_call',side_effect=lambda fn,**kw:fn()), patch('quant.data.extension_transport._open_provider',side_effect=[ConnectionResetError(),Response()]), patch('quant.data.extension_transport.time.sleep'):
        assert query('test','daily',{})==[{'ts_code':'x'}]
    with patch('quant.data.extension_transport.limited_tushare_call',side_effect=lambda fn,**kw:fn()), patch('quant.data.extension_transport._open_provider',side_effect=ConnectionResetError('private message')) as request, patch('quant.data.extension_transport.time.sleep'):
        with pytest.raises(ProviderError,match='^network_unavailable$'):
            query('test','daily',{},attempts=3)
        assert request.call_count==3
