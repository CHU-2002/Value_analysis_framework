import { api, selection, setHash } from '/app.js';

const STATUS = {queued:'排队', running:'正在生成', finished:'执行结束', failed:'执行失败', cancelled:'已取消'};
function node(tag, text, cls='') { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n; }
function labelled(text, input) { const n=node('label',text); n.append(input); return n; }
function select(options, title) { const n=node('select'); n.setAttribute('aria-label',title); for(const [value,text,disabled] of options){const o=node('option',text);o.value=value;o.disabled=!!disabled;n.append(o);}return n; }

export async function render(container, panel, data) {
  const payload=data || (await api(panel.endpoint)).data;
  const box=node('form',undefined,'agent-config'); container.append(box);
  const ticker=node('input');ticker.value=payload.company || '';ticker.required=true;ticker.placeholder='600887.SH / 00700.HK / AAPL';ticker.readOnly=!!payload.company;
  box.append(labelled('本次公司',ticker));
  const actions=payload.commands || [], agents=payload.agents || [];
  const action=select(actions.map(a=>[a.id,a.title]),'报告动作');
  const agent=select(agents.map(a=>[a.id,a.title+(a.installed?'':'（不可用，先安装并登录）'),!a.installed]),'执行 agent');
  const available=agents.find(a=>a.installed);if(available)agent.value=available.id;
  const model=select([],'本次模型');
  box.append(labelled('报告动作',action),labelled('Agent',agent),labelled('模型（仅本次）',model));
  const config=node('p',undefined,'agent-config-source'),reason=node('p',undefined,'agent-preflight');reason.setAttribute('role','status');
  const technical=node('details'),summary=node('summary','完整 argv（技术详情）'),argv=node('pre',undefined,'command-argv');technical.append(summary,argv);
  const submit=node('button','预检后确认生成（消耗模型额度）');submit.type='submit';submit.disabled=true;
  const result=node('div',undefined,'agent-job-result');
  box.append(config,reason,technical,submit,result);
  let revision=0,ready=false,preview='',pending=false,warning='';
  function models(){const a=agents.find(a=>a.id===agent.value);model.replaceChildren();if(!a)return;
    for(const m of [{id:'default',label:'沿用 CLI 默认'},...a.models]){const o=node('option',m.label||m.id);o.value=m.id;o.disabled=m.compatibility==='unavailable';model.append(o);}
    config.textContent=`目录来源：${a.source}；安装${a.installation}（${a.installation_evidence}）；登录${a.login}（${a.login_evidence}）。默认来源：${a.default.source}，${a.default.model || '默认模型未知'}。`;
  }
  async function check(){const version=++revision;ready=false;submit.disabled=true;reason.textContent='正在只读预检；尚未启动分析或任务。';
    const a=agents.find(a=>a.id===agent.value);if(!a?.installed){reason.textContent='本机没有可用 agent，请在终端安装并登录 Codex 或 Claude Code 后刷新。';return;}
    try{const q=new URLSearchParams({command:action.value,ticker:ticker.value,backend:agent.value,model:model.value});
      const p=(await api('/api/v1/agent/preflight?'+q)).data;if(version!==revision||!box.isConnected)return;
      ready=p.ready;preview=p.command_line||'';argv.textContent=preview || '命令行未就绪';
      warning=`登录未验证；${model.value==='default' ? (a.default.model || '默认模型未知') : model.value} 的账号授权/兼容性未验证。失败不会换模型或自动重试。`;
      reason.textContent=(p.reason || '预检通过；确认前仍未发起分析。')+' '+warning;
    }catch(e){if(version===revision)reason.textContent=e.message;}
    if(version===revision)submit.disabled=!ready || pending;
  }
  models();agent.onchange=()=>{models();void check();};model.onchange=action.onchange=ticker.oninput=()=>void check();
  async function follow(jobId){result.replaceChildren();const heading=node('p',`本任务 ${jobId}`),status=node('p'),task=node('a','去任务中心'),cancel=node('button','取消本任务');
    task.href='#commands?'+new URLSearchParams({company:ticker.value,job:jobId});cancel.type='button';cancel.onclick=async()=>{await api(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`,{method:'POST'});};
    const details=node('details'),title=node('summary','退出码、实际命令与原始日志'),log=node('pre',undefined,'job-log');details.append(title,log);result.append(heading,status,task,cancel,details);
    for(;;){if(!result.isConnected)return;const job=(await api(`/api/v1/jobs/${encodeURIComponent(jobId)}`)).data;
      status.textContent=`${STATUS[job.status]||'状态未知'} · 本任务公司 ${job.params.ticker} · Agent ${job.params.backend||'CLI配置'} · 请求模型 ${job.params.model||'CLI默认'}`;
      log.textContent=`退出码 ${job.exit_code ?? '尚未结束'}\n实际 argv：${(job.argv||[]).join(' ')}\n`+(job.log||[]).join('\n');
      if(!['running','queued'].includes(job.status)){cancel.remove();const outputs=(await api(`/api/v1/agent/jobs/${encodeURIComponent(jobId)}/artifacts`)).data;
        status.textContent += ` · CLI回报模型 ${outputs.actual_model || '未知'}`;
        if(outputs.message)result.append(node('p',outputs.message));
        for(const link of outputs.links||[]){const a=node('a',link.label);a.href=link.href;result.append(a,node('br'));}return;}
      await new Promise(r=>setTimeout(r,900));}
  }
  box.onsubmit=async e=>{e.preventDefault();if(!ready||pending)return;
    const command=actions.find(a=>a.id===action.value),a=agents.find(a=>a.id===agent.value);
    const message=`公司 ${ticker.value}\n动作 ${command.title}\nAgent ${a.title}\n模型 ${model.value==='default'?'沿用CLI默认：'+(a.default.model || '默认模型未知')+'；'+a.default.source:model.value}\n${warning}\n将联网、消耗模型额度；预计产出本公司${command.title}，增量动作还会产出变化报告。\n确认开始？`;
    if(!window.confirm(message))return;pending=true;submit.disabled=true;
    const params={ticker:ticker.value,backend:agent.value,model:model.value};
    const submittedCompany=selection.company || params.ticker;
    try{const job=(await api('/api/v1/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:command.id,params})})).data;
      // Keep the submitted company and job in a sharable URL. A later company change clears this job.
      if(!box.isConnected || (selection.company && selection.company!==submittedCompany))return;
      setHash('agent',{company:submittedCompany,job:job.id});await follow(job.id);
    }catch(e){if(box.isConnected)result.append(node('p',e.message));}finally{pending=false;if(box.isConnected)void check();}
  };
  // render returns before mountPanel attaches this card. Keep detach guards,
  // but start preflight/resume on the next task, after mount; background tabs may suspend frames.
  setTimeout(() => {
    if (!box.isConnected) return;
    void check();
    if(selection.job){void follow(selection.job).catch(error => {
      if (result.isConnected) result.append(node('p', error.message));
    });}
  }, 0);
}
