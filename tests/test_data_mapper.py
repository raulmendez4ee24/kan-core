from brain.data_mapper import auto_map_fields


def test_auto_map_fields_email_aliases() -> None:
    mapped, trace = auto_map_fields(
        {
            "client_email": "a@example.com",
            "correo": "b@example.com",
            "user_mail": "c@example.com",
        }
    )

    assert mapped["email"] in {"a@example.com", "b@example.com", "c@example.com"}
    assert trace["client_email"] == "email"
    assert trace["correo"] == "email"
    assert trace["user_mail"] == "email"
