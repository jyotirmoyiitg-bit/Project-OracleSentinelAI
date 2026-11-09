from sentinel.domain.models import CapabilityMatrix


def test_capability_matrix_preserves_oracle_view_names_in_serialized_data() -> None:
    capabilities = CapabilityMatrix(
        has_select_v_instance=True,
        has_select_v_diag_alert_ext=True,
    )

    serialized = capabilities.to_dict()

    assert serialized["has_select_v$instance"] is True
    assert serialized["has_select_v$diag_alert_ext"] is True
    assert "has_select_v_instance" not in serialized

    restored = CapabilityMatrix.from_dict(serialized)

    assert restored.has_select_v_instance is True
    assert restored.has_select_v_diag_alert_ext is True
