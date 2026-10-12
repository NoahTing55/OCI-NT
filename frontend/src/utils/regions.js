// 区域中文名映射（全站共享）
export const REGION_CN = {
  "us-phoenix-1": "凤凰城", "us-ashburn-1": "阿什本", "us-sanjose-1": "圣何塞", "us-chicago-1": "芝加哥",
  "ap-singapore-1": "新加坡", "ap-singapore-2": "新加坡西", "ap-tokyo-1": "东京", "ap-osaka-1": "大阪",
  "ap-seoul-1": "首尔", "ap-chuncheon-1": "春川", "ap-mumbai-1": "孟买", "ap-hyderabad-1": "海得拉巴",
  "ap-sydney-1": "悉尼", "ap-melbourne-1": "墨尔本", "ap-batam-1": "巴淡岛", "ap-kulai-2": "古来",
  "eu-frankfurt-1": "法兰克福", "eu-paris-1": "巴黎", "eu-marseille-1": "马赛", "eu-milan-1": "米兰",
  "eu-turin-1": "都灵", "eu-amsterdam-1": "阿姆斯特丹", "eu-madrid-1": "马德里", "eu-madrid-3": "马德里西",
  "eu-stockholm-1": "斯德哥尔摩", "eu-zurich-1": "苏黎世", "eu-jovanovac-1": "约瓦诺瓦茨",
  "uk-london-1": "伦敦", "uk-cardiff-1": "卡迪夫",
  "ca-toronto-1": "多伦多", "ca-montreal-1": "蒙特利尔",
  "sa-saopaulo-1": "圣保罗", "sa-vinhedo-1": "维涅杜", "sa-santiago-1": "圣地亚哥",
  "sa-valparaiso-1": "瓦尔帕莱索", "sa-bogota-1": "波哥大",
  "me-dubai-1": "迪拜", "me-abudhabi-1": "阿布扎比", "me-jeddah-1": "吉达", "me-riyadh-1": "利雅得",
  "il-jerusalem-1": "耶路撒冷",
  "mx-monterrey-1": "蒙特雷", "mx-queretaro-1": "克雷塔罗",
  "af-johannesburg-1": "约翰内斯堡", "af-casablanca-1": "卡萨布兰卡",
}

// 表格列：只显示中文名（如"凤凰城"）
export const regionCn = (id) => (id && REGION_CN[id]) || id || '-'

// 下拉/选择器：中文名 + ID（如"凤凰城 us-phoenix-1"）
export const fmtRegion = (id) => (id ? (REGION_CN[id] ? `${REGION_CN[id]} ${id}` : id) : '-')
