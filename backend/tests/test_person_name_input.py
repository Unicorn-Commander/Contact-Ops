import uuid
import pytest
from pydantic import ValidationError
from contact_ops.mcp.tools.people import UpdatePersonInput

@pytest.mark.parametrize("field", ["additional_names", "nicknames"])
def test_update_name_arrays_reject_null(field):
    with pytest.raises(ValidationError):
        UpdatePersonInput(person_id=uuid.uuid4(), etag="synthetic", **{field: None})
