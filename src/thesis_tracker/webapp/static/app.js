/* The local workbench's single entry module; views are added page by page. */
const TOKEN_COOKIE = "dsh_token";

/** The page never sees the token in a URL: the server hands it back as a cookie. */
export function tokenPresent() {
  return document.cookie.split(";").some((part) => part.trim().startsWith(`${TOKEN_COOKIE}=`));
}

export async function api(path) {
  const response = await fetch(path, {
    method: "GET",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  const payload = await response.json().catch(() => ({ error: "响应不是有效的 JSON。" }));
  if (!response.ok) {
    throw new Error(payload.error || `请求失败（HTTP ${response.status}）。`);
  }
  return payload;
}

async function main() {
  const main = document.getElementById("main");
  if (!tokenPresent()) {
    main.innerHTML = "<p>没有访问令牌。请用启动时终端里打印的网址重新打开本页。</p>";
    return;
  }
  try {
    const overview = await api("/api/overview");
    main.textContent = `数据库里有 ${Object.keys(overview.companies).length} 家公司。`;
  } catch (error) {
    main.textContent = error.message;
  }
}

main();
