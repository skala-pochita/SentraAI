// 사용자에게 보여 줄 오류. hint 는 다음에 할 일.
export class CliError extends Error {
  constructor(message, hint = '') { super(message); this.hint = hint }
}
