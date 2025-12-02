import http from 'k6/http';
import { check, sleep } from 'k6';

export let options = {
  vus: 30,            // 同時仮想ユーザー数（30人が同時にアクセス）
  duration: '3m',     // テスト時間（3分間）
  rampUp: '30s',      // 30秒でVUを段階的に増加
};

const BASE = __ENV.TARGET_URL || 'https://sciencebuddy.ngrok.app';

export default function () {
  const headers = { 'Content-Type': 'application/json' };

  // 1) チャット送信（予想フェーズの簡易API）
  let chatPayload = JSON.stringify({ message: '体積は大きくなる' });
  let chatRes = http.post(`${BASE}/chat`, chatPayload, { headers: headers });
  check(chatRes, {
    'chat status 2xx': (r) => r.status >= 200 && r.status < 300,
  });
  sleep(Math.random() * 1 + 0.5);

  // 2) 要約トリガー（POST で呼び出し）
  let sumRes = http.post(`${BASE}/summary?class=5&number=1&unit=空気の温度と体積`, null, { headers: headers });
  check(sumRes, {
    'summary status ok': (r) => r.status === 200 || r.status === 202 || r.status === 201 || r.status === 302 || r.status === 400,
  });

  sleep(1);
}
