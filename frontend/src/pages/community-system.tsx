import { useEffect, useRef, useState } from 'react';
import { Alert, AutoComplete, Button, Card, Dropdown, Form, Input, InputNumber, Select, Space, Switch } from 'antd';
import { DownOutlined } from '@ant-design/icons';
import { useAdminConfig } from '../components/admin';
import { normalizeSystem, readSystemFields, systemDefaults, systemPayload } from '../community-schema';

export default function SystemDefaults() {
  const [form] = Form.useForm(); const [groups] = useState(readSystemFields);
  const snapshot = useRef(systemDefaults); const signature = useRef(''); const attempted = useRef('');
  const [models, setModels] = useState<string[]>([]); const [modelOpen, setModelOpen] = useState(false); const [preview, setPreview] = useState('');
  const state = useAdminConfig('/api/config/system', form, config => { const normalized = normalizeSystem(config); snapshot.current = normalized; return normalized; });
  const blocked = !state.ready || !!state.pending;
  async function loadModels(force = false, open = true) {
    const payload = systemPayload(form.getFieldsValue(true), snapshot.current);
    const current = JSON.stringify([payload.landppt_api_key, payload.landppt_base_url]);
    if (!force && signature.current === current && models.length) { setModelOpen(open); return; }
    const result = await state.mutate('models', { api_key: payload.landppt_api_key, base_url: payload.landppt_base_url }, '', { endpoint: '/admin/api/system-landppt-models', success: false });
    if (!result) return;
    const list = Array.isArray(result.models) ? result.models.filter(Boolean).map(String).sort((a: string, b: string) => a === 'MODEL1' ? -1 : b === 'MODEL1' ? 1 : a.localeCompare(b)) : [];
    signature.current = current; setModels(list); setModelOpen(open);
    if (!String(form.getFieldValue('landppt_model') || '').trim() && list.length) form.setFieldValue('landppt_model', list[0]);
  }
  useEffect(() => {
    if (!state.ready || state.loading || state.readError) return;
    const current = JSON.stringify([form.getFieldValue('landppt_api_key'), form.getFieldValue('landppt_base_url')]);
    if (form.getFieldValue('landppt_api_key') && form.getFieldValue('landppt_base_url') && attempted.current !== current) { attempted.current = current; loadModels(false, false); }
  }, [state.ready, state.loading]);
  async function save() {
    try { await form.validateFields(); } catch { return; }
    const payload = systemPayload(form.getFieldsValue(true), snapshot.current);
    if (await state.mutate('save', { config: payload }, '', { success: '系统默认配置已保存' })) {
      snapshot.current = payload;
      if (payload.landppt_api_key && payload.landppt_base_url) await loadModels(true, false); else { setModels([]); signature.current = ''; }
    }
  }
  async function test() {
    try { await form.validateFields(['landppt_api_key', 'landppt_base_url', 'landppt_model']); } catch { return; }
    const payload = systemPayload(form.getFieldsValue(true), snapshot.current);
    const result = await state.mutate('test', { provider: 'landppt', api_key: payload.landppt_api_key, base_url: payload.landppt_base_url, model: payload.landppt_model }, '', { endpoint: '/api/ai/providers/test', success: '立铠 PPT 测试成功' });
    if (result) setPreview(result.response_preview || '连接正常');
  }
  return <div className="settings-stack community-system">
    <div className="settings-savebar"><span className="subtle" role="status">{state.loading ? '正在读取系统默认配置…' : '仅在用户未配置时提供默认值'}</span><Space><Space className="community-desktop-actions"><Button disabled={state.loading || !!state.pending} onClick={() => state.load(true)}>重新加载</Button><Button disabled={blocked || state.loading} loading={state.pending === 'test'} onClick={test}>测试 立铠 PPT</Button></Space><Dropdown trigger={['click']} menu={{ className: 'community-actions-menu', items: [{ key: 'reload', label: '重新加载' }, { key: 'test', label: '测试 立铠 PPT', disabled: !state.ready }], onClick: ({ key }) => key === 'reload' ? state.load(true) : test() }} disabled={state.loading || !!state.pending}><Button className="community-mobile-actions">更多 <DownOutlined aria-hidden /></Button></Dropdown><Button id="saveSystemDefaultsBtn" type="primary" disabled={blocked || state.loading} loading={state.pending === 'save'} onClick={save}>保存系统默认配置</Button></Space></div>
    {state.readError && <Alert type="error" title="配置加载失败" description={state.readError} action={<Button onClick={() => state.load()}>重试</Button>} />}{state.error && <Alert type="error" title={state.error} />}
    <Alert type="info" title="API Key、接入地址与工作流路径可留空，沿用用户自己的同名设置。" />
    <Form form={form} name="communitySystem" layout="vertical" requiredMark={false} initialValues={systemDefaults} disabled={!!state.pending} onValuesChange={changed => { if ('landppt_api_key' in changed || 'landppt_base_url' in changed) { setModels([]); signature.current = ''; setModelOpen(false); } }}>
      <div className="settings-stack">{groups.map(group => <Card key={group.title} size="small" className="system-config-block" title={group.title}>{group.description && <p className="subtle">{group.description}</p>}<div className="settings-fields">{group.fields.map(field => {
        const common = { id: field.id, placeholder: field.placeholder };
        const control = field.kind === 'checkbox' ? <Switch id={field.id} /> : field.kind === 'select' ? <Select {...common} options={field.options} /> : field.kind === 'number' ? <InputNumber {...common} min={field.min} max={field.max} step={field.step || 1} /> : field.kind === 'password' ? <Input.Password {...common} autoComplete="new-password" /> : field.name === 'landppt_model' ? <AutoComplete id={field.id} options={models.map(value => ({ value }))} open={modelOpen && models.length > 0} onOpenChange={setModelOpen} onSelect={() => setModelOpen(false)}><Input {...common} className="community-model-input" suffix={<Button className="community-model-toggle" type="text" aria-label="加载 立铠 PPT 模型" disabled={blocked} loading={state.pending === 'models'} onMouseDown={event => event.preventDefault()} onClick={() => loadModels()} icon={<DownOutlined aria-hidden />} />} /></AutoComplete> : <Input {...common} />;
        return <Form.Item key={field.name} name={field.name} label={field.label} htmlFor={field.id} extra={field.name === 'landppt_model' && state.ready && !models.length ? '可手动输入模型，也可加载列表。' : field.help} valuePropName={field.kind === 'checkbox' ? 'checked' : 'value'} rules={field.kind === 'number' ? [{ type: 'number', min: field.min, max: field.max, message: `请输入${field.min == null ? '' : `不小于 ${field.min}`}${field.max == null ? '' : `、不大于 ${field.max}`}的数值` }] : []}>{control}</Form.Item>;
      })}</div></Card>)}</div>
    </Form>{preview && <Alert className="admin-test-result" type="success" title="立铠 PPT 测试成功" description={preview} />}
  </div>;
}
