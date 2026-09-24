from services.jobs import DELIVERY_KEY_MAX_LENGTH, new_manual_delivery_key


def test_manual_delivery_key_fits_supabase_column():
    key = new_manual_delivery_key()

    assert key.startswith("manual:")
    assert len(key) == 39
    assert len(key) <= DELIVERY_KEY_MAX_LENGTH
