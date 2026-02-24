import openviking as ov
import tos
import os
from typing import List, Dict, Any, Optional
from loguru import logger
from vikingbot.config.loader import load_config

viking_resource_prefix = "viking://resources"


class VikingClient:
    def __init__(self, url: str, viking_path: str = "/bot_test/dutao/"):
        self.client = ov.SyncHTTPClient(url=url)
        self.client.initialize()
        config = load_config()
        openviking_config = config.tools.openviking
        self.tos_client = tos.TosClientV2(
            openviking_config.tos_ak,
            openviking_config.tos_sk,
            openviking_config.tos_endpoint,
            openviking_config.tos_region,
        )
        self.viking_path = viking_path

    def find(self, query: str, target_uri: Optional[str] = None) -> List[Any]:
        """搜索资源"""
        if target_uri:
            return self.client.find(query, target_uri=target_uri)
        return self.client.find(query)

    def add_resource(
        self, local_path: str, desc: str, target_path: Optional[str] = None, wait: bool = False
    ) -> Optional[Dict[str, Any]]:
        """添加资源到 Viking"""
        viking_target = f"{viking_resource_prefix}{self.viking_path}"
        if target_path:
            viking_target = f"{viking_resource_prefix}{target_path}"

        file_name = os.path.basename(local_path)
        object_key = f"{file_name}"

        try:
            self.tos_client.put_object_from_file(bucket_name, object_key, local_path)
            self.tos_client.set_object_expires(bucket_name, object_key, 1)
        except tos.exceptions.TosClientError as e:
            print(f"Failed to upload {local_path} to TOS: {e}")
            return None

        try:
            pre_signed_url = self.tos_client.pre_signed_url(
                tos.HttpMethodType.Http_Method_Get, bucket_name, object_key, expires=300
            ).signed_url
        except tos.exceptions.TosClientError as e:
            print(f"Failed to generate pre-signed URL for {object_key}: {e}")
            return None

        result = self.client.add_resource(
            path=pre_signed_url, target=viking_target, reason=desc, wait=wait
        )
        return result

    def read_source(self, viking_path: str) -> List[Any]:
        """读取源数据"""
        return self.client.find(viking_path)

    def list_resources(
        self, path: Optional[str] = None, recursive: bool = False
    ) -> List[Dict[str, Any]]:
        """列出资源"""
        target_path = f"{viking_resource_prefix}{self.viking_path}"
        if path:
            target_path = f"{viking_resource_prefix}{path}"

        entries = self.client.ls(target_path, recursive=recursive)
        return entries

    def read_content(self, uri: str, level: str = "abstract") -> str:
        """读取内容

        Args:
            uri: Viking URI
            level: 读取级别 ("abstract" - L0摘要, "overview" - L1概览, "read" - L2完整内容)
        """
        if level == "abstract":
            return self.client.abstract(uri)
        elif level == "overview":
            return self.client.overview(uri)
        elif level == "read":
            return self.client.read(uri)
        else:
            raise ValueError(f"Unsupported level: {level}")

    def search(self, query: str, target_uri: Optional[str] = None) -> List[Any]:
        """仅搜索资源"""
        # session = self.client.session()
        if target_uri is not None:
            target_uri = f"{viking_resource_prefix}/{target_uri}"
        else:
            target_uri = f"{viking_resource_prefix}/"
        result = self.client.search(query, target_uri=target_uri)
        logger.warning(f"Viking result: {result}")
        return result

    def grep(self, uri: str, pattern: str, case_insensitive: bool = False) -> Dict[str, Any]:
        """通过模式（正则表达式）搜索内容"""
        if not uri.startswith(viking_resource_prefix):
            uri = f"{viking_resource_prefix}{uri}"
        return self.client.grep(uri, pattern, case_insensitive=case_insensitive)

    def glob(self, pattern: str, uri: Optional[str] = None) -> Dict[str, Any]:
        """通过 glob 模式匹配文件"""
        if uri:
            if not uri.startswith(viking_resource_prefix):
                uri = f"{viking_resource_prefix}{uri}"
        else:
            uri = viking_resource_prefix
        return self.client.glob(pattern, uri=uri)

    def move_resource(self, from_uri: str, to_uri: str) -> Dict[str, Any]:
        """移动资源"""
        return self.client.mv(from_uri, to_uri)

    def delete_resource(self, uri: str, recursive: bool = False) -> Dict[str, Any]:
        """删除资源"""
        return self.client.rm(uri, recursive=recursive)

    def create_link(self, from_uri: str, to_uris: List[str], reason: str = "") -> Dict[str, Any]:
        """创建资源链接"""
        return self.client.link(from_uri, to_uris, reason=reason)

    def get_relations(self, uri: str) -> List[Dict[str, str]]:
        """获取资源关联"""
        return self.client.relations(uri)

    def delete_link(self, from_uri: str, to_uri: str) -> Dict[str, Any]:
        """删除资源链接"""
        return self.client.unlink(from_uri, to_uri)

    def wait_processed(self) -> Dict[str, Any]:
        """等待处理完成"""
        return self.client.wait_processed()

    def close(self):
        """关闭客户端"""
        self.client.close()


if __name__ == "__main__":
    client = VikingClient("")
    # res = client.list_resources()
    res = client.search("头有点疼")
    # res = client.read_content("viking://resources/bot_test/dutao/test/")
    # res = client.add_resource("/Users/bytedance/.openviking/test.py", "一段代码")
    print(res)
    result_strs = []
    for i, result in enumerate(res, 1):
        result_strs.append(f"{i}. {str(result.uri)}")
    print("\n".join(result_strs))
    client.close()
