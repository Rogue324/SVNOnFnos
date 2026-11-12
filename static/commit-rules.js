// Commit policy controls are attached when a repository detail is displayed.
new MutationObserver(() => {
  const actions = document.querySelector('.detail-actions');
  if (!actions || actions.querySelector('[data-commit-rules]') || !state.detail) return;
  const button = document.createElement('button');
  button.className = 'button small';
  button.textContent = '提交规则与通知';
  button.dataset.commitRules = '';
  button.addEventListener('click', async () => {
    try {
      const repo = state.detail.name;
      const config = await api(`${repoApi(repo)}/commit-rules`);
      openModal('提交规则与通知', `<p class="field-help">提交说明不足时拒绝提交。通知以 JSON POST 发送，失败不会撤销已完成的提交。已有 hooks 会继续执行。</p><label>提交说明最少字符数<input name="minMessageLength" type="number" min="0" max="10000" value="${esc(config.minMessageLength)}" required></label><label>通知地址（可留空）<input name="notificationUrl" type="url" maxlength="2048" value="${esc(config.notificationUrl)}" placeholder="https://example.com/webhook"></label>`, async values => {
        await api(`${repoApi(repo)}/commit-rules`, {method: 'PUT', body: {minMessageLength: Number(values.minMessageLength), notificationUrl: values.notificationUrl}});
        toast('提交规则已保存');
      });
    } catch (error) { toast(error.message); }
  });
  actions.appendChild(button);
}).observe(document.getElementById('view-content'), {childList: true, subtree: true});
