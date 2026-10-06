"""安全列表规则管理：抢机成功后默认放行所有端口。

参照 OCI-Start 的 SecurityRuleServiceImpl：
检查子网安全列表是否已有 protocol=all、source=0.0.0.0/0 的入站规则，
没有则追加。注意 OCI 的 PUT 是全量替换，必须先 GET 拿到现有规则再追加。
"""
import logging

from app.core.oci_client import OciClient


logger = logging.getLogger(__name__)

# 全端口放行规则：所有协议、所有来源
ALLOW_ALL_RULE = {
    "protocol": "all",
    "source": "0.0.0.0/0",
    "description": "oci-panel: allow all ingress",
}


def _has_allow_all(ingress_rules: list[dict]) -> bool:
    """检查入站规则中是否已存在全端口放行（protocol=all + source=0.0.0.0/0）。"""
    for rule in ingress_rules or []:
        if rule.get("protocol") == "all" and rule.get("source") == "0.0.0.0/0":
            return True
    return False


async def ensure_allow_all_ingress(client: OciClient, subnet_id: str) -> bool:
    """确保子网的安全列表放行所有端口。

    流程：GET 子网 → 取第一个 security-list-id → GET 安全列表 →
    检查 ingress 规则 → 没有则追加后 PUT 全量替换。

    返回 True 表示规则已存在（无需操作或添加成功），False 表示失败。
    失败只记日志，不抛异常（不影响抢机主流程）。
    """
    try:
        subnet = await client.get_subnet(subnet_id)
        seclist_ids = subnet.get("securityListIds") or []
        if not seclist_ids:
            logger.warning("子网 %s 没有绑定安全列表，跳过放行", subnet_id)
            return False
        seclist_id = seclist_ids[0]

        seclist = await client.get_security_list(seclist_id)
        ingress_rules = seclist.get("ingressSecurityRules") or []
        if _has_allow_all(ingress_rules):
            logger.info("安全列表 %s 已有全端口放行规则，无需操作", seclist_id)
            return True

        new_rules = list(ingress_rules) + [dict(ALLOW_ALL_RULE)]
        resp = await client.update_security_list(seclist_id, new_rules)
        if resp.status_code in (200, 202):
            logger.info("安全列表 %s 已追加全端口放行规则", seclist_id)
            return True
        logger.warning("更新安全列表 %s 失败：HTTP %s %s",
                       seclist_id, resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        logger.warning("放行所有端口失败（子网 %s）：%s", subnet_id, e)
        return False
