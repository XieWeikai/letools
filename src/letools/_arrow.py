from __future__ import annotations

from typing import Any

import pyarrow as pa

from letools.plugins import DatasetSource


_PRIMITIVES = {
    "bool": pa.bool_(),
    "float16": pa.float16(),
    "float32": pa.float32(),
    "float64": pa.float64(),
    "int8": pa.int8(),
    "int16": pa.int16(),
    "int32": pa.int32(),
    "int64": pa.int64(),
    "uint8": pa.uint8(),
    "uint16": pa.uint16(),
    "uint32": pa.uint32(),
    "uint64": pa.uint64(),
}


def _replace_leaf(data_type: pa.DataType, leaf: pa.DataType) -> pa.DataType:
    if pa.types.is_list(data_type):
        return pa.list_(_replace_leaf(data_type.value_type, leaf))
    if pa.types.is_large_list(data_type):
        return pa.large_list(_replace_leaf(data_type.value_type, leaf))
    if pa.types.is_fixed_size_list(data_type):
        return pa.list_(_replace_leaf(data_type.value_type, leaf), data_type.list_size)
    return leaf


def canonical_data_schema(source: DatasetSource) -> pa.Schema:
    schema = source.read_episode(source.episodes[0]).schema
    fields = []
    for field in schema:
        feature = source.metadata.features.get(field.name)
        dtype = feature.get("dtype") if feature else None
        if dtype in _PRIMITIVES:
            fields.append(
                pa.field(
                    field.name,
                    _replace_leaf(field.type, _PRIMITIVES[dtype]),
                    nullable=field.nullable,
                    metadata=field.metadata,
                )
            )
        else:
            fields.append(field)
    return pa.schema(fields, metadata=schema.metadata)


def cast_data_table(table: pa.Table, schema: pa.Schema) -> pa.Table:
    if table.schema.equals(schema, check_metadata=False):
        return table.replace_schema_metadata(schema.metadata)
    return table.cast(schema)


def numpy_to_arrow(values: Any) -> pa.Array:
    """Convert a dense NumPy array without materializing nested Python lists.

    The first dimension is the Arrow row dimension. Remaining dimensions become
    nested fixed-size lists, preserving tensor shape in Parquet efficiently.
    """

    import numpy as np

    array = np.asarray(values)
    if array.ndim == 0:
        raise ValueError("An episode feature must have a frame dimension")
    if array.ndim == 1:
        return pa.array(array)
    result: pa.Array = pa.array(array.reshape(-1))
    for size in reversed(array.shape[1:]):
        result = pa.FixedSizeListArray.from_arrays(result, size)
    return result


def _nested_value_shape(value: Any) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    if not value:
        raise ValueError("Cannot infer a feature shape from an empty list.")

    child_shapes = {_nested_value_shape(item) for item in value}
    if len(child_shapes) != 1:
        raise ValueError("Cannot infer a feature shape from a ragged list.")
    return (len(value), *child_shapes.pop())


def normalize_feature_shapes(source: DatasetSource, features: dict[str, dict[str, Any]]) -> None:
    """Correct redundant leading singleton dimensions using actual episode data."""
    table = source.read_episode(source.episodes[0])
    for key, feature in features.items():
        if feature.get("dtype") in {"image", "video"} or key not in table.column_names:
            continue

        value = next((item.as_py() for item in table[key] if item.is_valid), None)
        if value is None:
            continue

        value_shape = _nested_value_shape(value)
        actual_shape = value_shape or (1,)
        declared_shape = tuple(feature["shape"])
        if declared_shape == actual_shape:
            continue

        prefix_length = len(declared_shape) - len(actual_shape)
        prefix = declared_shape[:prefix_length]
        suffix = declared_shape[prefix_length:]
        if prefix_length > 0 and all(size == 1 for size in prefix) and suffix == actual_shape:
            feature["shape"] = list(actual_shape)
            continue

        raise ValueError(
            f"Feature {key!r} declares shape {declared_shape}, but Parquet data has shape "
            f"{actual_shape}. Only redundant leading singleton dimensions can be corrected."
        )
