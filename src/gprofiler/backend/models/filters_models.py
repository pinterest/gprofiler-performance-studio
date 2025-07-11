#
# Copyright (C) 2023 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from enum import Enum
from typing import Dict, List, Optional

from backend.models import CamelModel
from gprofiler_dev.tags import CONTAINER_KEY, HOSTNAME_KEY, INSTANCE_TYPE_KEY, K8S_OBJ_KEY


class EditFilterTag(CamelModel):
    new_filter_tag: str


class FilterTypes(str, Enum):
    CONTAINER_KEY = CONTAINER_KEY
    HOSTNAME_KEY = HOSTNAME_KEY
    K8S_OBJ_KEY = K8S_OBJ_KEY
    INSTANCE_TYPE_KEY = INSTANCE_TYPE_KEY


class RQLCompareOperators(str, Enum):
    eq_op = "$eq"
    neq_op = "$neq"


class RQLLogicOperators(str, Enum):
    and_op = "$and"
    or_op = "$or"


class FilterTag(CamelModel):
    name: str
    samples: Optional[str]


class RQLFilter(CamelModel):
    filter: Dict[RQLLogicOperators, List[Dict[FilterTypes, Dict[RQLCompareOperators, str]]]]

    def get_formatted_filter(self) -> str:
        res = []
        for logic_op, expressions in self.filter.items():
            for expression in expressions:
                for key, cmp_op_value in expression.items():
                    for cmp_op, value in cmp_op_value.items():
                        res.append("_".join([key, cmp_op, value]))
                res.append(logic_op)
        return "__".join(res[:-1]).replace("$", "")

    # TODO: Update schema_extra for Pydantic v2
    # @classmethod
    # def __get_pydantic_json_schema__(cls, core_schema, handler):
    #     schema = handler(core_schema)
    #     # Custom schema modifications would go here
    #     return schema


class GetRQLFilter(RQLFilter):
    id: int


class PutRQLFilter(RQLFilter):
    id: int
