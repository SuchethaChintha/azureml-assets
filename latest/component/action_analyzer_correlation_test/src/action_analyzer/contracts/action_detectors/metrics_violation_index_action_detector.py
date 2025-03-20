# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Metrics violation index action detector class."""

import os
import traceback
from typing import List
from action_analyzer.contracts.action_detectors.action_detector import ActionDetector
from action_analyzer.contracts.actions.action import Action
from action_analyzer.contracts.actions.metrics_violation_index_action import MetricsViolationIndexAction
from action_analyzer.contracts.llm_client import LLMClient
import pandas
from action_analyzer.contracts.utils.detector_utils import (
    extract_retrieval_info_from_root_span,
    get_retrieval_score,
    get_query_intention,
    generate_index_action_samples,
    peform_correlation_test
)
from shared_utilities.constants import (
    INDEX_ID_COLUMN,
    INDEX_SCORE_LLM_COLUMN,
    DEFAULT_TOPIC_NAME,
    INDEX_CONTENT_COLUMN,
    PROMPT_COLUMN,
    INVALID_LLM_SCORE,
    METRICS_VIOLATION_THRESHOLD,
    GOOD_METRICS_THRESHOLD
)


class MetricsViolationIndexActionDetector(ActionDetector):
    """Metrics violation index action detector class."""

    def __init__(self,
                 index_id: str,
                 violated_metrics: List[str],
                 correlation_test_method: str,
                 correlation_test_pvalue_threshold: float,
                 query_intention_enabled: str,
                 positive_metric_threshold=GOOD_METRICS_THRESHOLD,
                 negative_metric_threshold=METRICS_VIOLATION_THRESHOLD,
                 preprocessed_data=pandas.DataFrame()) -> None:
        """Create a metrics violation index action detector.

        Args:
            index_id(str): the hashed index id.
            violated_metrics(List[str]): violated e2e metrics
            correlation_test_method(str): test method for correlation test. e.g. ttest.
            correlation_test_pvalue_threshold(float): p-value threshold for correlation test to generate action.
            query_intention_enabled(str): enable llm generated summary. Accepted values: true or false.
            positive_metric_threshold(int): (Optional) e2e metric threshold to mark the query as positive.
            negative_metric_threshold(int): (Optional) e2e metric threshold to mark the query as negative.
            preprocessed_data(pandas.DataFrame): (Optional) preprocessed data. If passed, skip the preprocess step.
        """
        self.index_id = index_id
        self.violated_metrics = violated_metrics
        self.correlation_test_method = correlation_test_method
        self.correlation_test_pvalue_threshold = correlation_test_pvalue_threshold
        self.positive_metric_threshold = positive_metric_threshold
        self.negative_metric_threshold = negative_metric_threshold
        super().__init__(query_intention_enabled, preprocessed_data)

    def preprocess_data(self, df: pandas.DataFrame, llm_client: LLMClient):
        """Preprocess the data for action detector.

        Args:
            df(pandas.DataFrame): input pandas dataframe.
            llm_client(LLMClient): LLM client used to get some llm scores/info for action.
        """
        try:
            if not self.preprocessed_data.empty:
                print("Preprocessed data is available. Skip executing preprocess.")
                return
            print("Start to run MetricsViolationIndexActionDetector.")
            preprocessed_df = extract_retrieval_info_from_root_span(df, self.index_id)

            # get llm retrieval score
            preprocessed_df[INDEX_SCORE_LLM_COLUMN] = preprocessed_df.apply(get_retrieval_score,
                                                                            axis=1,
                                                                            args=(llm_client,))
            self.preprocessed_data = preprocessed_df[preprocessed_df[INDEX_SCORE_LLM_COLUMN] != INVALID_LLM_SCORE]
        except Exception as e:
            print("MetricsViolationIndexActionDetector preprocess failed with error", e)
            print(traceback.format_exc())

    def detect(self, llm_client: LLMClient, aml_deployment_id=None) -> List[Action]:
        """Detect the action.

        Args:
            llm_client(LLMClient): LLM client used to get some llm scores/info for action.
            aml_deployment_id(str): (Optional) aml deployment id for the action.

        Returns:
            List[Action]: list of actions.
        """
        action_list = []
        if not self.preprocessed_data.empty:
            print("MetricsViolationIndexActionDetector start to detect actions.")
            df = self.preprocessed_data
            try:
                for metric in self.violated_metrics:
                    low_metric_score_df = df[df[metric] < self.negative_metric_threshold]
                    high_metric_score_df = df[df[metric] >= self.positive_metric_threshold]

                    t_stat, p_value = peform_correlation_test(high_metric_score_df,
                                                              low_metric_score_df,
                                                              self.correlation_test_method)
                    if t_stat > 0 and p_value < self.correlation_test_pvalue_threshold:
                        print(f"Generating action for metric {metric}.")
                        action = self.generate_action(llm_client,
                                                      metric,
                                                      1-p_value,
                                                      low_metric_score_df,
                                                      high_metric_score_df,
                                                      aml_deployment_id)
                        print(f"Positive sample size: {len(action.positive_samples)}.")
                        print(f"Negative sample size: {len(action.negative_samples)}.")
                        action_list.append(action)
            except Exception as e:
                print("MetricsViolationIndexActionDetector detect failed with error", e)
                print(traceback.format_exc())
        return action_list

    def generate_action(self,
                        llm_client: LLMClient,
                        metric: str,
                        confidence_score: float,
                        low_metric_score_df: pandas.DataFrame,
                        high_metric_score_df: pandas.DataFrame,
                        aml_deployment_id: str) -> MetricsViolationIndexAction:
        """Generate action from the dataframe.

        Args:
            llm_client(LLMClient): LLM client used to get some llm scores/info for action.
            metric(str): the violated metric for action.
            confidence_score(float): LLM client used to get some llm scores/info for action.
            low_metric_score_df(pandas.DataFrame): the data with low metric score.
            high_metric_score_df(pandas.DataFrame): the data with high metric score.
            aml_deployment_id(str): aml deployment id for the action.

        Returns:
            MetricsViolationIndexAction: the generated low retrieval score index action.
        """
        query_intention = get_query_intention(low_metric_score_df[PROMPT_COLUMN].to_list(), llm_client) if self.query_intention_enabled == "true" else DEFAULT_TOPIC_NAME  # noqa: E501
        print("Got query intention: ", query_intention)
        positive_samples = generate_index_action_samples(high_metric_score_df, False)
        negative_samples = generate_index_action_samples(low_metric_score_df, True)

        index_content = low_metric_score_df.iloc[0][INDEX_CONTENT_COLUMN]
        index_asset_id = low_metric_score_df.iloc[0][INDEX_ID_COLUMN]
        return MetricsViolationIndexAction(index_asset_id,
                                           index_content,
                                           metric,
                                           confidence_score,
                                           query_intention,
                                           aml_deployment_id,
                                           os.environ.get("AZUREML_RUN_ID", None),
                                           positive_samples,
                                           negative_samples)
