// Explicit opt-in: this browser flow calls paid embedding and chat providers.
// The API uses an in-memory DB and only synthetic users and knowledge.
const { test, expect } = require('@playwright/test')

test.skip(process.env.GAMECS_E2E_LIVE_AI !== '1', 'Requires explicit live AI opt-in and provider keys')
test.setTimeout(120000)

test('player receives a live answer with a synthetic knowledge source', async ({ page }) => {
  await page.goto('http://127.0.0.1:5175/')
  await page.getByRole('textbox', {name:'用户名'}).fill('e2e_player')
  await page.getByRole('textbox', {name:'密码'}).fill('test-password')
  await page.getByRole('button', {name:'登录', exact:false}).click()
  await page.getByRole('button', {name:'客服助手'}).click()
  await page.getByRole('combobox', {name:'选择游戏'}).selectOption({label:'测试游戏'})
  await page.getByRole('textbox', {name:'你的问题'}).fill('登录失败应该怎么检查网络和客户端？')
  await page.getByRole('button', {name:'发送问题'}).click()
  await expect(page.getByText('参考：测试知识')).toBeVisible({timeout:90000})
  await expect(page.locator('.message:not(.user) .message-text').last()).not.toBeEmpty()
})
