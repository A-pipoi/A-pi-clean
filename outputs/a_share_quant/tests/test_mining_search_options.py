import pytest

from quant.mining.search_options import validate_search_options


def test_all_seeds_share_the_same_total_search_cap():
    assert validate_search_options({"seeds":[1,2],"generations":10,"population_size":50})==[1,2]
    assert validate_search_options({}) is None
    with pytest.raises(ValueError,match="2,000"):
        validate_search_options({"seeds":[1,2,3],"generations":20,"population_size":100})


@pytest.mark.parametrize("payload", [{"seeds":[1,1]},{"seeds":[True,2]},{"fitness_mode":"invalid"},{"temporal_cv":"false"},{"correlation_lambda":"nan"},{"field_subset":"close"},{"operator_subset":[]}])
def test_bad_search_controls_fail_before_provider_calls(payload):
    with pytest.raises((ValueError,TypeError)):
        validate_search_options(payload)
