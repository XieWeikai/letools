from __future__ import annotations

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
