import React, { useState, useEffect } from 'react'
import { api } from '../api'
import type { SubscriptionPlan, UserSubscription, SubscribeResult } from '../types'
import { CreditCard, Zap, Clock, CheckCircle, XCircle, AlertTriangle, Loader2, RefreshCw, Copy, Check } from 'lucide-react'

interface Props {
  currentUser: { role: string }
  onClose: () => void
}

function SubscriptionPage({ currentUser, onClose }: Props) {
  const [plans, setPlans] = useState<SubscriptionPlan[]>([])
  const [mySub, setMySub] = useState<UserSubscription | null>(null)
  const [selectedPlan, setSelectedPlan] = useState<SubscriptionPlan | null>(null)
  const [subscribeResult, setSubscribeResult] = useState<SubscribeResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [subscribing, setSubscribing] = useState(false)
  const [checking, setChecking] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    setLoading(true)
    try {
      const plansData = await api.subscriptions.plans.public()
      setPlans(plansData)
    } catch {}
    try {
      const subData = await api.subscriptions.my()
      setMySub(subData)
    } catch {}
    setLoading(false)
  }

  const handleSubscribe = async (plan: SubscriptionPlan) => {
    setSelectedPlan(plan)
    setSubscribeResult(null)
    setSubscribing(true)
    try {
      const result = await api.subscriptions.subscribe(plan.id)
      setSubscribeResult(result)
      if (result.status === 'active' || result.status === 'pending') {
        await loadData()
      }
    } catch (e: any) {
      setSubscribeResult({ subscription_id: 0, status: 'error', plan_name: e.message })
    }
    setSubscribing(false)
  }

  const [checkError, setCheckError] = useState<string | null>(null)

  const handleCheckPayment = async () => {
    const subId = subscribeResult?.subscription_id || mySub?.id
    if (!subId) return
    setChecking(true)
    setCheckError(null)
    try {
      const result = await api.subscriptions.checkPayment(subId)
      if (result.status === 'active') {
        setSubscribeResult(null)
        setSelectedPlan(null)
        await loadData()
      } else {
        setCheckError('Payment not yet confirmed. Please wait and try again.')
      }
    } catch (e: any) {
      setCheckError(e.message || 'Failed to check payment')
    }
    setChecking(false)
  }

  const handleCancel = async () => {
    const subId = mySub?.id
    if (!subId) return
    setCancelling(true)
    try {
      await api.subscriptions.cancel(subId)
      setMySub(null)
      setSubscribeResult(null)
      setSelectedPlan(null)
    } catch {}
    setCancelling(false)
  }

  const handleCopy = async (text: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 2000) } catch {}
  }

  const pendingInvoice = subscribeResult?.payment_request || (mySub?.status === 'pending' ? mySub.payment_request : null)
  const pendingPlanName = subscribeResult?.plan_name || mySub?.plan_name
  const pendingSubId = subscribeResult?.subscription_id || mySub?.id
  const isActive = mySub?.status === 'active'
  const daysLeft = mySub?.expires_at
    ? Math.max(0, Math.ceil((new Date(mySub.expires_at).getTime() - Date.now()) / (1000 * 60 * 60 * 24)))
    : 0

  if (loading) {
    return (
      <div className="fixed inset-0 bg-theme-overlay/60 z-50 flex items-center justify-center">
        <div className="bg-theme-bg-secondary rounded-xl p-8">
          <Loader2 className="w-8 h-8 animate-spin text-theme-accent-text" />
        </div>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 bg-theme-overlay/60 z-50 flex items-center justify-center p-4">
      <div className="bg-theme-bg-secondary rounded-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto border border-theme-border-light shadow-2xl">
        <div className="p-6 border-b border-theme-border flex items-center justify-between">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <CreditCard className="w-5 h-5 text-theme-accent-text" />
            Subscription
          </h2>
          <button onClick={onClose} className="p-1 hover:bg-theme-bg-elevated rounded text-theme-muted hover:text-theme-text-secondary">
            <XCircle className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 space-y-6">
          {/* Current subscription status */}
          {mySub && (
            <div className={`p-4 rounded-xl border ${
              isActive ? 'bg-theme-accent/20 border-theme-msg-user/50' :
              mySub.status === 'pending' ? 'bg-theme-amber/20 border-theme-amber/50' :
              'bg-theme-danger/20 border-theme-danger/50'
            }`}>
              <div className="flex items-center gap-2 mb-2">
                {isActive ? <CheckCircle className="w-5 h-5 text-theme-accent-text" /> :
                 mySub.status === 'pending' ? <Clock className="w-5 h-5 text-theme-amber" /> :
                 <AlertTriangle className="w-5 h-5 text-theme-danger-text" />}
                <span className="font-semibold">
                  {isActive ? `Active: ${mySub.plan_name}` :
                   mySub.status === 'pending' ? `Pending: ${mySub.plan_name}` :
                   `${mySub.status}: ${mySub.plan_name}`}
                </span>
              </div>
              {isActive && mySub.expires_at && (
                <p className="text-sm text-theme-subtle">
                  {daysLeft <= 3 ? (
                    <span className="text-theme-amber font-medium">
                      <AlertTriangle className="w-4 h-4 inline mr-1" />
                      Renewal reminder: {daysLeft} {daysLeft === 1 ? 'day' : 'days'} remaining.
                      {daysLeft === 0 ? ' Top up now to avoid interruption.' : ' Time to top up!'}
                    </span>
                  ) : (
                    <span>{daysLeft} {daysLeft === 1 ? 'day' : 'days'} remaining</span>
                  )}
                </p>
              )}
              {mySub.status === 'pending' && mySub.payment_checking_id && (
                <p className="text-sm text-theme-amber mt-1">Payment pending. Click "Check Payment" on your invoice to confirm.</p>
              )}
              <p className="text-xs text-theme-subtle mt-2">
                Tokens used: <span className="font-mono text-theme-text-secondary">{mySub.token_usage.toLocaleString()}</span>
                {mySub.plan_token_limit != null && (
                  <span> / <span className={mySub.token_usage >= mySub.plan_token_limit ? 'text-theme-danger-text font-semibold' : 'text-theme-text-secondary'}>{mySub.plan_token_limit.toLocaleString()}</span></span>
                )}
                {mySub.plan_token_limit != null && mySub.token_usage >= mySub.plan_token_limit && (
                  <span className="text-theme-danger-text ml-2 font-medium">LIMIT REACHED</span>
                )}
              </p>
              {(isActive || mySub.status === 'pending') && (
                <button
                  onClick={handleCancel}
                  disabled={cancelling}
                  className="mt-3 w-full flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-theme-danger/20 hover:bg-theme-danger/30 text-theme-danger-text border border-theme-danger/30 transition-colors disabled:opacity-50"
                >
                  {cancelling ? <Loader2 className="w-4 h-4 animate-spin" /> : <XCircle className="w-4 h-4" />}
                  Cancel Subscription
                </button>
              )}
            </div>
          )}

          {/* Free plan is always visible and active by default */}
          {!mySub && (
            <div className="p-4 rounded-xl border bg-theme-bg-elevated/50 border-theme-border-light">
              <div className="flex items-center gap-2 mb-2">
                <Zap className="w-5 h-5 text-theme-subtle" />
                <span className="font-semibold">Free Plan</span>
              </div>
              <p className="text-sm text-theme-subtle">
                You are on the Free plan by default. Subscribe to a paid plan for higher limits.
              </p>
            </div>
          )}

          {/* Subscribe result (invoice QR code) */}
          {pendingInvoice && (
            <div className="p-4 rounded-xl border bg-theme-bg-elevated border-theme-border-light space-y-3">
              <h3 className="text-sm font-semibold text-theme-accent-text">Lightning Invoice</h3>
              <p className="text-xs text-theme-subtle">Pay this invoice with any Lightning wallet to activate your {pendingPlanName} plan.</p>
              <div className="bg-theme-bg rounded-lg p-3">
                <pre className="text-xs text-theme-text-secondary break-all whitespace-pre-wrap font-mono select-all">
                  {pendingInvoice}
                </pre>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => handleCopy(pendingInvoice!)}
                  className="flex items-center gap-1 px-3 py-1.5 bg-theme-bg-hover hover:bg-theme-bg-active rounded-lg text-xs"
                >
                  {copied ? <Check className="w-3.5 h-3.5 text-theme-accent-text" /> : <Copy className="w-3.5 h-3.5" />}
                  {copied ? 'Copied' : 'Copy Invoice'}
                </button>
                <button
                  onClick={handleCheckPayment}
                  disabled={checking}
                  className="flex items-center gap-1 px-3 py-1.5 bg-theme-accent hover:bg-theme-accent-hover rounded-lg text-xs disabled:opacity-50"
                >
                  {checking ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                  Check Payment
                </button>
              </div>
              {checkError && (
                <p className="text-theme-danger-text text-xs mt-2">{checkError}</p>
              )}
            </div>
          )}

          {subscribeResult?.status === 'active' && (
            <div className="p-4 rounded-xl border bg-theme-bg-elevated border-theme-border-light">
              <p className="text-theme-accent-text text-sm">Subscription activated!</p>
            </div>
          )}

          {subscribeResult?.status === 'error' && (
            <div className="p-4 rounded-xl border bg-theme-bg-elevated border-theme-border-light">
              <p className="text-theme-danger-text text-sm">{subscribeResult.plan_name}</p>
            </div>
          )}

          {/* Available plans */}
          <div>
            <h3 className="text-sm font-semibold text-theme-subtle mb-3">Available Plans</h3>
            <div className="space-y-3">
              {plans.filter(p => p.name !== 'Free' || !mySub).map(plan => {
                const isCurrent = mySub?.plan_id === plan.id && isActive
                const isFree = plan.name === 'Free'
                return (
                  <div
                    key={plan.id}
                    className={`p-4 rounded-xl border transition-colors ${
                      isCurrent ? 'bg-theme-accent/20 border-theme-accent/50' : 'bg-theme-bg-elevated/50 border-theme-border-light hover:border-theme-border-light'
                    }`}
                  >
                    <div className="flex items-start justify-between">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-semibold">{plan.name}</span>
                          {isCurrent && <span className="text-xs px-2 py-0.5 bg-theme-accent/30 text-theme-accent-text rounded-full">Current</span>}
                          {!plan.enabled && <span className="text-xs px-2 py-0.5 bg-theme-danger/30 text-theme-danger-text rounded-full">Disabled</span>}
                        </div>
                        <p className="text-sm text-theme-subtle mt-1">
                          {plan.duration_days > 0 ? `${plan.duration_days} days` : 'Unlimited'}
                          {plan.token_limit ? ` · ${plan.token_limit.toLocaleString()} tokens` : ' · No token limit'}
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-xl font-bold text-theme-accent-text">
                          {plan.price_sats === 0 ? 'Free' : `${plan.price_sats.toLocaleString()} sats`}
                        </p>
                        {plan.duration_days > 0 && plan.price_sats > 0 && (
                          <p className="text-xs text-theme-muted">~{(plan.price_sats / plan.duration_days).toFixed(0)} sats/day</p>
                        )}
                      </div>
                    </div>
                    {!isCurrent && (
                      <button
                        onClick={() => handleSubscribe(plan)}
                        disabled={subscribing}
                        className={`mt-3 w-full flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                          plan.name === 'Free'
                            ? 'bg-theme-bg-hover text-theme-subtle hover:bg-theme-bg-active'
                            : 'bg-theme-accent hover:bg-theme-accent-hover disabled:opacity-50'
                        }`}
                      >
                        {subscribing && selectedPlan?.id === plan.id ? (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <Zap className="w-4 h-4" />
                        )}
                        {plan.price_sats === 0 ? 'Switch to Free' : 'Subscribe'}
                      </button>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default SubscriptionPage
