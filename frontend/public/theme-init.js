// 첫 페인트 전에 테마를 적용해 깜빡임을 막는다 (light | dark | system)
(function () {
  var m = 'system'
  try { m = localStorage.getItem('theme') || 'system' } catch (e) { /* 저장소 차단 */ }
  var dark = m === 'dark' || (m === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
})()

