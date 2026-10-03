import React, { useState, useEffect } from 'react'
import axios from 'axios'
import { useAuth } from '../auth/AuthContext'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export default function ReviewsModal({ charger, isOpen, onClose }) {
  const { isAuthenticated, user, setIsAuthModalOpen } = useAuth()

  const [reviewsData, setReviewsData] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [rating, setRating] = useState(5)
  const [hoverRating, setHoverRating] = useState(0)
  const [reviewText, setReviewText] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitSuccess, setSubmitSuccess] = useState(false)
  const [submitError, setSubmitError] = useState(null)

  useEffect(() => {
    if (isOpen && charger?.charger_id) {
      fetchReviews()
    }
  }, [isOpen, charger?.charger_id])

  const fetchReviews = async () => {
    setIsLoading(true)
    try {
      const res = await axios.get(`${API_BASE_URL}/chargers/${charger.charger_id}/reviews`)
      setReviewsData(res.data)
    } catch (err) {
      console.error('Failed to fetch reviews:', err)
    } finally {
      setIsLoading(false)
    }
  }

  if (!isOpen || !charger) return null

  const handleSubmitReview = async (e) => {
    e.preventDefault()
    setIsSubmitting(true)
    setSubmitError(null)
    setSubmitSuccess(false)

    try {
      const res = await axios.post(`${API_BASE_URL}/chargers/${charger.charger_id}/reviews`, {
        rating,
        review_text: reviewText.trim(),
      })
      setSubmitSuccess(true)
      setReviewText('')
      // Refresh reviews list
      await fetchReviews()
      setTimeout(() => setSubmitSuccess(false), 3000)
    } catch (err) {
      console.error('Failed to submit review:', err)
      setSubmitError(err.response?.data?.detail || 'Failed to submit review.')
    } finally {
      setIsSubmitting(false)
    }
  }

  const avgRating = reviewsData?.avg_rating || 4.5
  const count = reviewsData?.review_count || 0
  const reviewsList = reviewsData?.recent_reviews || []

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fade-in">
      <div className="relative w-full max-w-lg bg-[#0D121D] border border-white/10 rounded-2xl shadow-2xl p-6 text-slate-100 flex flex-col max-h-[85vh] overflow-hidden space-y-4">
        {/* Header */}
        <div className="flex items-start justify-between border-b border-white/5 pb-3">
          <div>
            <span className="label-subhead text-amber-400 flex items-center gap-1.5">
              <span>★</span>
              <span>Community Reviews</span>
            </span>
            <h2 className="text-base font-medium text-white tracking-tight mt-0.5">
              {charger.name}
            </h2>
            <p className="label-quiet text-xs mt-0.5">
              {charger.operator ? `${charger.operator} · ` : ''}{charger.city || 'Mysuru'}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-white transition p-1.5 rounded-lg hover:bg-white/5"
          >
            ✕
          </button>
        </div>

        {/* Rating Summary Card */}
        <div className="p-4 rounded-xl bg-white/[0.03] border border-white/5 flex items-center justify-between">
          <div className="flex items-baseline gap-3">
            <span className="text-3xl font-light text-amber-300 font-mono">
              {avgRating.toFixed(1)}
            </span>
            <div className="space-y-0.5">
              <div className="flex text-amber-400 text-sm">
                {'★'.repeat(Math.round(avgRating))}
                {'☆'.repeat(5 - Math.round(avgRating))}
              </div>
              <p className="label-quiet text-xs font-mono">{count} verified driver reviews</p>
            </div>
          </div>
          <span className="px-2.5 py-1 rounded-full bg-amber-400/10 text-amber-300 text-xs font-mono">
            {(charger.reliability * 100).toFixed(0)}% Reliability
          </span>
        </div>

        {/* Reviews List & Submission Form */}
        <div className="overflow-y-auto space-y-4 flex-1 pr-1 custom-scrollbar">
          {/* Review Submission Box */}
          <form
            onSubmit={handleSubmitReview}
            className="p-3.5 rounded-xl bg-white/[0.02] border border-white/10 space-y-3"
          >
            <span className="text-xs font-medium text-white block">Rate your charging experience</span>

            {/* Interactive Star Picker */}
            <div className="flex items-center gap-1.5">
              {[1, 2, 3, 4, 5].map((star) => (
                <button
                  key={star}
                  type="button"
                  onClick={() => setRating(star)}
                  onMouseEnter={() => setHoverRating(star)}
                  onMouseLeave={() => setHoverRating(0)}
                  className="text-2xl transition hover:scale-110 focus:outline-none"
                >
                  <span
                    className={
                      star <= (hoverRating || rating) ? 'text-amber-400' : 'text-slate-600'
                    }
                  >
                    ★
                  </span>
                </button>
              ))}
              <span className="text-xs label-quiet ml-2 font-mono">{rating} / 5 Stars</span>
            </div>

            <textarea
              rows={2}
              value={reviewText}
              onChange={(e) => setReviewText(e.target.value)}
              placeholder="How was the charger speed, connector health, or parking accessibility?"
              className="w-full bg-white/5 border border-white/10 rounded-xl p-2.5 text-xs text-white focus:outline-none focus:border-cockpit-teal/50 transition placeholder:text-slate-500"
            />

            {submitSuccess && (
              <p className="text-xs text-emerald-400">✓ Thank you! Your review has been recorded.</p>
            )}
            {submitError && (
              <p className="text-xs text-rose-400">⚠️ {submitError}</p>
            )}

            <div className="flex items-center justify-between pt-1">
              <span className="label-quiet text-[11px]">
                Posting as: <strong className="text-slate-300">{user?.name || 'Verified EV Driver'}</strong>
              </span>
              <button
                type="submit"
                disabled={isSubmitting}
                className="btn-primary px-4 py-1.5 text-xs font-medium"
              >
                {isSubmitting ? 'Submitting...' : 'Submit Review'}
              </button>
            </div>
          </form>

          {/* Recent Reviews Feed */}
          <div className="space-y-2.5">
            <span className="label-subhead text-white block">Recent Feedback</span>

            {isLoading && (
              <div className="py-6 text-center text-xs label-quiet">Loading feedback...</div>
            )}

            {!isLoading && reviewsList.length === 0 && (
              <div className="py-6 text-center border border-dashed border-white/10 rounded-xl label-quiet text-xs">
                No reviews recorded yet. Be the first driver to rate this station!
              </div>
            )}

            {!isLoading &&
              reviewsList.map((rev) => (
                <div
                  key={rev.id}
                  className="p-3 rounded-xl bg-white/[0.02] border border-white/5 space-y-1.5"
                >
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium text-white">{rev.user_name}</span>
                    <div className="flex text-amber-400 text-xs">
                      {'★'.repeat(rev.rating)}
                      {'☆'.repeat(5 - rev.rating)}
                    </div>
                  </div>
                  {rev.review_text && (
                    <p className="text-xs text-slate-300 leading-relaxed font-sans">{rev.review_text}</p>
                  )}
                  <span className="label-quiet text-[10px] block font-mono">
                    {new Date(rev.created_at).toLocaleDateString([], {
                      month: 'short',
                      day: 'numeric',
                      year: 'numeric',
                    })}
                  </span>
                </div>
              ))}
          </div>
        </div>
      </div>
    </div>
  )
}
