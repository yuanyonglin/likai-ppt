import { useState } from 'react';
import { Alert, Button, Card, Form, Input, InputNumber, Radio, Result, Select, Space, Switch, Tag } from 'antd';
import { type ApiData } from '../api';
import { AdminPage, useAdminConfig } from '../components/admin';

const mailDefaults: ApiData = { email_provider: 'smtp', smtp_host: '', smtp_port: 465, smtp_user: '', smtp_password: '', smtp_use_ssl: true, smtp_from_email: '', smtp_from_name: '立铠 PPT', resend_api_key: '', resend_from_email: '', resend_from_name: '立铠 PPT' };
function mailConfig(config: ApiData) {
  const values = Object.fromEntries(Object.keys(mailDefaults).map(key => [key, config[key] ?? mailDefaults[key]]));
  values.email_provider = String(values.email_provider).toLowerCase() === 'resend' ? 'resend' : 'smtp';
  return values;
}
function Feedback({ state }: { state: ReturnType<typeof useAdminConfig> }) {
  return <>{state.readError && <Alert type="error" showIcon title="配置加载失败" description={state.readError} action={<Button onClick={() => state.load()}>重试</Button>} />}{state.error && <Alert type="error" showIcon title={state.error} />}</>;
}
function SaveBar({ state, title, onSave }: { state: ReturnType<typeof useAdminConfig>; title: string; onSave: () => void }) {
  return <div className="settings-savebar"><span className="subtle" role="status">{state.loading ? '正在读取已保存的配置…' : state.ready ? '保存后应用当前配置' : '请先成功加载配置'}</span><Space><Button disabled={state.loading || !!state.pending} onClick={() => state.load(true)}>重新加载</Button><Button type="primary" disabled={state.loading || !state.ready || !!state.pending} loading={state.pending === 'save'} onClick={onSave}>{title}</Button></Space></div>;
}

function OAuth({ data }: { data: ApiData }) {
  const [form] = Form.useForm();
  const defaults = Object.fromEntries(['github', 'linuxdo'].flatMap(provider => [['enabled', false], ['client_id', ''], ['client_secret', ''], ['callback_url', ''], ...(provider === 'github' ? [['callback_use_request_host', false]] : [])].map(([field, value]) => [`${provider}_${field}`, value])));
  const state = useAdminConfig('/admin/api/oauth', form, config => Object.fromEntries(Object.keys(defaults).map(key => { const [provider, ...field] = key.split('_'); return [key, config[provider]?.[field.join('_')] ?? defaults[key]]; })));
  const values = Form.useWatch([], form) || defaults;
  async function save() {
    let fields: ApiData; try { fields = await form.validateFields(); } catch { return; }
    const payload: ApiData = {};
    for (const provider of ['github', 'linuxdo']) payload[provider] = { enabled: !!fields[`${provider}_enabled`], client_id: fields[`${provider}_client_id`]?.trim() || '', client_secret: fields[`${provider}_client_secret`]?.trim() || '', callback_url: fields[`${provider}_callback_url`]?.trim() || '', ...(provider === 'github' ? { callback_use_request_host: !!fields.github_callback_use_request_host } : {}) };
    await state.mutate('save', payload);
  }
  return <AdminPage title="OAuth 设置" description="配置第三方登录凭据与回调地址" credits={data.creditsEnabled}>
    <div className="settings-stack"><Feedback state={state} /><SaveBar state={state} title="保存设置" onSave={save} />
      <Form form={form} layout="vertical" requiredMark={false} initialValues={defaults} disabled={!!state.pending}>
        <div className="oauth-providers">{['github', 'linuxdo'].map(provider => {
          const title = provider === 'github' ? 'GitHub' : 'LinuxDo';
          const enabled = !!values[`${provider}_enabled`]; const configured = !!(values[`${provider}_client_id`]?.trim() && values[`${provider}_client_secret`]?.trim());
          const rules = [{ validator: (_: unknown, value: string) => !form.getFieldValue(`${provider}_enabled`) || value?.trim() ? Promise.resolve() : Promise.reject(new Error(`启用 ${title} 登录前请填写凭据`)) }];
          return <Card size="small" title={`${title} OAuth`} key={provider} extra={<Tag color={enabled ? configured ? 'success' : 'warning' : 'default'}>{enabled ? configured ? '已启用' : '待补全' : '未启用'}</Tag>}>
            <Form.Item name={`${provider}_enabled`} label={`启用 ${title} 登录`} valuePropName="checked"><Switch id={`${provider}_enabled`} /></Form.Item>
            <div className="settings-fields"><Form.Item name={`${provider}_client_id`} label="Client ID" dependencies={[`${provider}_enabled`]} rules={rules}><Input autoComplete="off" /></Form.Item><Form.Item name={`${provider}_client_secret`} label="Client Secret" dependencies={[`${provider}_enabled`]} rules={rules}><Input.Password autoComplete="new-password" /></Form.Item></div>
            <Form.Item name={`${provider}_callback_url`} label="回调地址" extra={`留空由服务端生成 /auth/${provider}/callback 地址。`}><Input placeholder={`https://your-domain.com/auth/${provider}/callback`} /></Form.Item>
            {provider === 'github' && <Form.Item name="github_callback_use_request_host" label="优先使用当前请求域名拼接回调地址" valuePropName="checked"><Switch /></Form.Item>}
          </Card>;
        })}</div>
      </Form>
    </div>
  </AdminPage>;
}

function Mail({ data }: { data: ApiData }) {
  const [form] = Form.useForm(); const [testForm] = Form.useForm(); const [inviteForm] = Form.useForm();
  const state = useAdminConfig('/admin/api/smtp', form, mailConfig);
  const provider = Form.useWatch('email_provider', form) || 'smtp';
  const [testResult, setTestResult] = useState(''); const [inviteResult, setInviteResult] = useState('');
  const blocked = state.loading || !state.ready || !!state.pending;
  function payload() { return mailConfig(form.getFieldsValue(true)); }
  function mailFields() { return Object.keys(mailDefaults).filter(key => key === 'email_provider' || key.startsWith(provider + '_')); }
  async function save() { try { await form.validateFields(mailFields()); await state.mutate('save', payload()); } catch { /* Validation appears inline. */ } }
  async function test() {
    let values: ApiData; try { values = await testForm.validateFields(); await form.validateFields(mailFields()); } catch { return; }
    const config = payload(); const required = provider === 'smtp' ? ['smtp_host', 'smtp_user'] : ['resend_api_key', 'resend_from_email'];
    const missing = required.filter(key => !config[key]?.trim());
    if (missing.length) { form.setFields(missing.map(name => ({ name, errors: ['发送测试邮件前请填写此项'] }))); form.scrollToField(missing[0], { focus: true }); return; }
    setTestResult(''); const result = await state.mutate('test', { ...config, test_email: values.test_email.trim(), test_subject: values.test_subject || '', test_content: values.test_content || '' }, '/test');
    if (result) setTestResult(result.message || '测试邮件已发送');
  }
  async function testInvite() {
    let values: ApiData; try { values = await inviteForm.validateFields(); } catch { return; }
    setInviteResult(''); const result = await state.mutate('invite', { test_emails: values.test_emails.trim(), channel: values.channel, expires_in_days: values.expires_in_days ?? null }, '/test-invite');
    if (result) setInviteResult(result.message || '邀请码测试邮件已发送');
  }
  const emailRule = [{ type: 'email' as const, message: '请输入有效的邮箱地址' }];
  return <AdminPage title="邮件服务设置" description="配置发信服务、发件人并验证邮件发送" credits={data.creditsEnabled}>
    <div className="settings-stack"><Feedback state={state} /><SaveBar state={state} title="保存邮件设置" onSave={save} />
      <Form form={form} layout="vertical" requiredMark={false} initialValues={mailDefaults} disabled={!!state.pending}>
        <Card size="small" title="邮件配置"><Form.Item name="email_provider" label="邮件服务"><Radio.Group name="email_provider" options={[{ label: 'SMTP', value: 'smtp' }, { label: 'Resend', value: 'resend' }]} /></Form.Item>
          <div id="smtpConfigSection" hidden={provider !== 'smtp'}><div className="settings-fields">
            <Form.Item name="smtp_host" label="SMTP 服务器地址"><Input placeholder="smtp.example.com" /></Form.Item><Form.Item name="smtp_port" label="端口" extra="SSL 通常使用 465，TLS 使用 587" rules={[{ type: 'number', min: 1, max: 65535, message: '端口需为 1–65535' }]}><InputNumber min={1} max={65535} precision={0} /></Form.Item>
            <Form.Item name="smtp_user" label="SMTP 用户名"><Input autoComplete="off" /></Form.Item><Form.Item name="smtp_password" label="SMTP 密码 / 授权码" extra="部分邮箱需要使用授权码"><Input.Password autoComplete="new-password" /></Form.Item>
            <Form.Item name="smtp_from_email" label="发件人邮箱" extra="留空使用 SMTP 用户名" rules={emailRule}><Input type="email" /></Form.Item><Form.Item name="smtp_from_name" label="发件人名称"><Input /></Form.Item>
          </div><Form.Item name="smtp_use_ssl" label="使用 SSL 加密" valuePropName="checked"><Switch /></Form.Item></div>
          <div id="resendConfigSection" hidden={provider !== 'resend'}><Form.Item name="resend_api_key" label="Resend API Key"><Input.Password placeholder="re-…" autoComplete="new-password" /></Form.Item><div className="settings-fields"><Form.Item name="resend_from_email" label="发件人邮箱" extra="必须是 Resend 中已验证的域名邮箱" rules={emailRule}><Input type="email" /></Form.Item><Form.Item name="resend_from_name" label="发件人名称"><Input /></Form.Item></div></div>
        </Card>
      </Form>
      <Card size="small" title="发送测试邮件"><Form form={testForm} layout="vertical" requiredMark={false} disabled={blocked} onFinish={test}><div className="settings-fields">
        <Form.Item name="test_email" label="测试收件邮箱" rules={[{ required: true, message: '请输入收件邮箱' }, ...emailRule]}><Input type="email" /></Form.Item><Form.Item name="test_subject" label="邮件主题（可选）"><Input placeholder="留空使用默认主题" /></Form.Item></div><Form.Item name="test_content" label="邮件内容（可选，支持 HTML）"><Input.TextArea rows={3} placeholder="留空使用默认测试内容" /></Form.Item><Button htmlType="submit" disabled={blocked} loading={state.pending === 'test'}>发送测试邮件</Button></Form>{testResult && <Alert className="admin-test-result" type="success" title={testResult} showIcon />}</Card>
      <Card size="small" title="发送邀请码测试邮件"><Alert type="info" title="将生成真实可用的邀请码并通过已保存的邮件配置发送，每个收件人收到独立邀请码。" className="page-error" />
        <Form form={inviteForm} layout="vertical" requiredMark={false} initialValues={{ channel: 'universal' }} disabled={blocked} onFinish={testInvite}>
          <Form.Item name="test_emails" label="收件邮箱" extra="支持多个，逗号、分号或换行分隔" rules={[{ required: true, whitespace: true, message: '请输入至少一个收件邮箱' }]}><Input.TextArea rows={3} /></Form.Item><div className="settings-fields"><Form.Item name="channel" label="适用渠道"><Select options={[{ label: '通用', value: 'universal' }, { label: '邮箱注册', value: 'mail' }, { label: 'GitHub', value: 'github' }, { label: 'LinuxDo', value: 'linuxdo' }]} /></Form.Item><Form.Item name="expires_in_days" label="有效天数（可选）" rules={[{ type: 'number', min: 1, message: '有效天数至少为 1 天' }]}><InputNumber min={1} precision={0} /></Form.Item></div><Button htmlType="submit" disabled={blocked} loading={state.pending === 'invite'}>生成邀请码并发送测试邮件</Button>
        </Form>{inviteResult && <Alert className="admin-test-result" type="success" title={inviteResult} showIcon />}
      </Card>
    </div>
  </AdminPage>;
}

export default function AdminSettings({ data }: { data: ApiData }) {
  if (!data.user?.is_admin) return <Result status="403" title="需要管理员权限" />;
  return data.section === 'oauth' ? <OAuth data={data} /> : <Mail data={data} />;
}
