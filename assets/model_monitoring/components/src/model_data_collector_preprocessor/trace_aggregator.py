# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Internal logic for Trace Aggregator step of Gen AI preprocessor component."""


from pyspark.sql import DataFrame, Row
from pyspark.sql.types import StructType
from pyspark.sql.functions import collect_list, struct
from typing import List
from .span_tree_utils import SpanTree, SpanTreeNode
from .genai_preprocessor_df_schemas import (
    _get_aggregated_trace_log_spark_df_schema,
)
from shared_utilities.io_utils import init_spark


def _construct_aggregated_trace_entry(span_tree: SpanTree, output_schema: StructType) -> tuple:
    """Build an aggregated trace tuple for RDD from a span tree."""
    span_dict = span_tree.root_span.to_dict(datetime_to_str=False)
    span_dict['root_span'] = span_tree.to_json_str()
    return tuple(span_dict.get(fieldName, None) for fieldName in output_schema.fieldNames())


def _construct_span_tree(span_rows: List[Row]) -> SpanTree:
    """Build a span tree from the raw span rows."""
    span_list = [SpanTreeNode(row) for row in span_rows]
    tree = SpanTree(span_list)
    return tree


def _aggregate_span_logs_to_trace_logs(grouped_row: Row, output_schema: StructType):
    """Aggregate grouped span logs into trace logs."""
    tree = _construct_span_tree(grouped_row.span_rows)
    return _construct_aggregated_trace_entry(tree, output_schema)


def process_spans_into_aggregated_traces(span_logs: DataFrame, require_trace_data: bool) -> DataFrame:
    """Group span logs into aggregated trace logs."""
    spark = init_spark()
    output_trace_schema = _get_aggregated_trace_log_spark_df_schema()

    if not require_trace_data:
        print("Skip processing of spans into aggregated traces.")
        return spark.createDataFrame(data=[], schema=output_trace_schema)

    print("Processing spans into aggregated traces...")

    def _aggregate_span_logs_to_trace_logs(grouped_row: Row, output_schema: StructType):
        """Aggregate grouped span logs into trace logs."""
        span_list = [SpanTreeNode(row) for row in grouped_row]
        tree = SpanTree(span_list)
        if tree.root_span is None:
            return tuple()
        span_dict = tree.root_span.to_dict(datetime_to_str=False)
        span_dict['root_span'] = tree.to_json_str()
        return tuple(span_dict.get(fieldName, None) for fieldName in output_schema.fieldNames())

    grouped_spans_df = span_logs.groupBy('trace_id').agg(
        collect_list(
            struct(span_logs.schema.fieldNames())
        ).alias('span_rows')
    )

    all_aggregated_traces = grouped_spans_df \
        .rdd \
        .map(lambda x: _aggregate_span_logs_to_trace_logs(x, output_trace_schema)) \
        .toDF(output_trace_schema)

    print("Aggregated Trace DF:")
    all_aggregated_traces.show()
    all_aggregated_traces.printSchema()
    return all_aggregated_traces
