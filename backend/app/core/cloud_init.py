"""cloud-init user_data 构建：新实例首次启动时自动设置 root 密码并开启 SSH 密码登录。

思路参考 OCI-Start 的 SystemScriptShell.getShell(passwd)：通过实例 metadata
的 user_data（base64 编码后的 #cloud-config）下发，开机即生效，无需等实例
启动后再 SSH 进去改。
"""
import base64

# 密码用占位符替换而非 f-string，避免密码里的 {} 破坏格式化；
# chpasswd 用 YAML 块标量（|），密码特殊字符无需转义。
_CLOUD_CONFIG_TEMPLATE = """#cloud-config
ssh_pwauth: yes
chpasswd:
  list: |
    root:__ROOT_PASSWORD__
  expire: false
write_files:
  - path: /tmp/oci_panel_root_access.sh
    permissions: '0700'
    content: |
      #!/bin/bash
      # OCI 面板一键建机：允许 root 密码登录
      sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
      sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
      grep -q '^PasswordAuthentication yes' /etc/ssh/sshd_config || echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config
      grep -q '^PermitRootLogin yes' /etc/ssh/sshd_config || echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config
      systemctl restart sshd || service ssh restart || service sshd restart
runcmd:
  - bash /tmp/oci_panel_root_access.sh
  - rm -f /tmp/oci_panel_root_access.sh
"""


def build_root_password_script(password: str) -> str:
    """构建设置 root 密码的 cloud-init 脚本，返回 base64 编码后的字符串。

    直接作为 launch_instance 的 user_data 参数传入。
    """
    yaml_text = _CLOUD_CONFIG_TEMPLATE.replace("__ROOT_PASSWORD__", password)
    return base64.b64encode(yaml_text.encode("utf-8")).decode("ascii")
