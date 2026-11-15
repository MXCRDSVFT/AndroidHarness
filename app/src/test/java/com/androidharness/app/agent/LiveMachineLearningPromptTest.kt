package com.androidharness.app.agent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LiveMachineLearningPromptTest {
    @Test fun attachesPolicyWithoutDroppingBaseInstructions() {
        val combined = LiveMachineLearningPrompt.attach("Base system rules")
        assertTrue(combined.startsWith("Base system rules\n\n"))
        assertTrue(combined.contains("Live Machine Learning / Verified Adaptation"))
        assertTrue(combined.contains("Do not claim persistence without a successful write receipt"))
    }

    @Test fun remainsDeterministicAcrossChats() {
        val first = LiveMachineLearningPrompt.attach("A")
        assertEquals(first, LiveMachineLearningPrompt.attach("A"))
        assertTrue(LiveMachineLearningPrompt.attach("B").startsWith("B"))
    }
}
