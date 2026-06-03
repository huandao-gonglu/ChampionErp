export function useCurrency() {
  const formatMoney = (value: number, currency = 'MXN') =>
    new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency,
      maximumFractionDigits: 2,
    }).format(value)

  const formatPercent = (value: number) => `${value.toFixed(2)}%`

  return {
    formatMoney,
    formatPercent,
  }
}
