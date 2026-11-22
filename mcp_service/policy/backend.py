"""政策向量服务和 Milvus 的独立发布、检索接入。"""
import httpx
from pymilvus import DataType, MilvusClient
from config import load_config
from config.limits import PolicyLimits
from implementations.policy.source import DOCUMENT_ID, VERSION
from mcp_service.policy.search import COLLECTION, normalize_vector
from model.policy.search import PolicyEntry, PolicyVectorRecord
from model.policy.status import PolicyEntryType, PolicySearchMode

FIELDS = list(PolicyEntry.model_fields)


# 连接固定政策集合及配置文件中的向量、重排服务。
class PolicyBackend:
    # 创建独立客户端，不为模型调用暴露数据库参数。
    def __init__(self):
        policy = load_config().policy
        if policy is None:
            raise ValueError('配置文件缺少 policy 设置')
        self.client = MilvusClient(uri=policy.milvus_uri, token=policy.milvus_token, timeout=PolicyLimits.MILVUS_CONNECT_TIMEOUT_SECONDS)
        self.base = policy.model_base.rstrip('/')

    # 关闭 Milvus 连接。
    def close(self):
        self.client.close()

    # 用 BGE-M3 编码并验证响应向量。
    def embed(self, text):
        with httpx.Client(timeout=PolicyLimits.MODEL_TIMEOUT_SECONDS, trust_env=False) as http:
            response = http.post(self.base + '/bge/v1/embeddings', json={'model': 'BAAI/bge-m3', 'input': [text]})
            response.raise_for_status()
            return normalize_vector(response.json()['data'][0]['embedding'])

    # 在服务端按固定文档和当前版本过滤后召回最多八条。
    def recall(self, vector: list[float], mode: PolicySearchMode) -> list[PolicyEntry]:
        # region 固定当前文档版本，正式范围额外限定为正式条款，拒绝未知范围
        match mode:
            case PolicySearchMode(value=PolicySearchMode.FORMAL.value):
                entry_type_filter = f' and entry_type == "{PolicyEntryType.POLICY.value}"'
            case PolicySearchMode(value=PolicySearchMode.CONSULTATION.value):
                entry_type_filter = ''
            case _:
                raise ValueError('无效检索范围')
        expression = f'document_id == "{DOCUMENT_ID}" and version == "{VERSION}"{entry_type_filter}'
        # endregion

        # region 按限定范围召回候选并核对条目契约
        results = self.client.search(COLLECTION, data=[vector], anns_field='embedding', filter=expression,
                                     limit=PolicyLimits.MAX_RECALLED_ENTRIES, output_fields=FIELDS, search_params={'metric_type': 'IP'}, timeout=PolicyLimits.MILVUS_SEARCH_TIMEOUT_SECONDS)
        return [PolicyEntry.model_validate(hit['entity']) for hit in results[0]]
        # endregion

    # 重排全部候选并严格验证返回的索引，最多返回前三条。
    def rerank(self, query: str, candidates: list[PolicyEntry]) -> list[PolicyEntry]:
        # region 将本轮候选原文与查询一起送重排
        with httpx.Client(timeout=PolicyLimits.MODEL_TIMEOUT_SECONDS, trust_env=False) as http:
            response = http.post(self.base + '/reranker/v1/rerank', json={
                'model': 'BAAI/bge-reranker-v2-m3', 'query': query,
                'documents': [item.text for item in candidates]})
            response.raise_for_status()
            results = response.json()['results']
        # endregion

        # region 重排索引完整且无重复时按顺序取前三条，否则拒绝本轮结果
        indexes = [item['index'] for item in results]
        if len(indexes) != len(candidates) or set(indexes) != set(range(len(candidates))):
            raise ValueError('无效重排结果')
        return [candidates[index] for index in indexes[:PolicyLimits.MAX_RANKED_ENTRIES]]
        # endregion

    # 仅创建缺失的专用集合和固定索引；不删除已有集合。
    def create_collection(self):
        # region 已有政策集合时核对字段和索引，发现不兼容时停止发布
        if self.client.has_collection(COLLECTION):
            description = self.client.describe_collection(COLLECTION)
            fields = {field['name']: field for field in description['fields']}
            lengths = PolicyLimits.FIELD_BYTES
            if (set(fields) != set(lengths) | {'embedding'} or description['auto_id']
                    or description.get('enable_dynamic_field')
                    or not fields['chunk_id'].get('is_primary')
                    or any(fields[name]['type'] != DataType.VARCHAR or fields[name]['params']['max_length'] != size
                           for name, size in lengths.items())
                    or fields['embedding']['type'] != DataType.FLOAT_VECTOR
                    or fields['embedding']['params']['dim'] != PolicyLimits.VECTOR_DIMENSIONS):
                raise ValueError('现有政策集合结构不兼容')
            index = self.client.describe_index(COLLECTION, 'embedding')
            if index.get('index_type') != 'FLAT' or index.get('metric_type') != 'IP':
                raise ValueError('现有政策集合索引不兼容')
            return
        # endregion

        # region 专用政策集合缺失时创建所需字段和向量索引
        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
        for name, size in PolicyLimits.FIELD_BYTES.items():
            schema.add_field(name, DataType.VARCHAR, max_length=size, is_primary=name == 'chunk_id')
        schema.add_field('embedding', DataType.FLOAT_VECTOR, dim=PolicyLimits.VECTOR_DIMENSIONS)
        indexes = self.client.prepare_index_params()
        indexes.add_index(field_name='embedding', index_type='FLAT', metric_type='IP')
        self.client.create_collection(COLLECTION, schema=schema, index_params=indexes, timeout=PolicyLimits.MILVUS_WRITE_TIMEOUT_SECONDS)
        # endregion

    # 只写入本演示政策条目，不触碰其他集合或历史版本。
    def publish(self, entries: list[PolicyEntry]) -> None:
        # region 构建并校验全部政策向量记录，再按稳定主键写入专用集合
        records = [PolicyVectorRecord(**entry.model_dump(), embedding=self.embed(entry.text)) for entry in entries]
        self.create_collection()
        self.client.upsert(COLLECTION, [record.model_dump(mode='json') for record in records], timeout=PolicyLimits.MILVUS_WRITE_TIMEOUT_SECONDS)
        self.client.flush(COLLECTION, timeout=PolicyLimits.MILVUS_WRITE_TIMEOUT_SECONDS)
        # endregion
