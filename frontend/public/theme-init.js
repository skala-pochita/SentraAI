// 첫 페인트 전에 테마를 적용해 깜빡임을 막는다 (light | dark)
(function () {
  var m = 'light'
  try { m = localStorage.getItem('theme') || 'light' } catch (e) { /* 저장소 차단 */ }
  document.documentElement.dataset.theme = m === 'dark' ? 'dark' : 'light'
})()
